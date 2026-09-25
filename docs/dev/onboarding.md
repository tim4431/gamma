# Onboarding: tours and contextual guides

**Status: the engine, the two manual tours (first paper, AI chat), seven
triggered tours and five hints are built.** The welcome page, synced
`onboarding` pref, first-sign-in invitation and checklist are still design.
What exists: `frontend/src/guide/` (anchor registry, event bus, trigger rules
in `triggers.js`, `useGuide`, `GuideOverlay`, one file per tour in `tours/`,
the hints in `tours/hints.js`), the node test `tests/guide.test.mjs` and the
e2e scenarios `guide.mjs` (first paper), `contextualGuide.mjs` (AI chat) and
`triggeredGuide.mjs` (offers and hints). The first tour is a welcome card,
the add-a-paper demo, then the user's own highlight on that paper, a typed
note demonstration, an `llm` label demonstration, and a final spotlight on
the Home button. Returning to the library emits `home.opened`, shows Done,
and completes the tour automatically; Finish can also close it. Progress is
a localStorage key per account (`gamma-guide:<account>:<tourId>`; the first
tour keeps its older `gamma-guide:first-run`), not yet the synced pref. The
seeded welcome page in `gamma/seed.py` is unchanged (guest workspaces only,
hard-coded block tuples).

## Manual tours (implemented)

The account menu's **Tours** submenu lists the tours that can start where the
user is (`guide.startable()`): a tour's `requires` hold and its first step's
anchor, or the control that reveals it, is on screen. A tour with `show`
brings up its own surface first (AI chat opens the chat). So **Your first
paper** and **AI chat** are always listed; **Sharing a page** and **Editing
tables** on any page you can edit, **Handwriting** on any PDF you can edit —
each begins by having you make the thing it explains when there is none yet
(see "Steps that have the user make something"); **Working together** only
while someone else is on the page. Hints are never listed. `?guide=` URLs
never start a tour, and the chat header has no guide button. Cards use short
titles, without body paragraphs. In Chinese a tour is 教程.

AI chat starts in the message box and types `summarize the paper for me`, then
points to voice input. Existing drafts are restored after the example; an
empty composer keeps the example ready to edit. The tour never submits a
message or starts recording. If the current page carries a PDF and its window
is visible, it demonstrates Ctrl-drag and points to the resulting image in
chat context. Hidden PDFs, notes pages, and phone chat omit those steps.

The paper tour frames the actual attention equation, types a note through
CodeMirror without replacing existing writing, adds the `llm` label through
the normal label field, and ends at Home. Successful user actions briefly
show Done before advancing automatically.

## Triggered tours and hints (implemented)

A tour with a `trigger` is also offered by itself, once per `version`, right
**after** the thing it explains happened, never on mere contact with a
control (focusing the chat composer offers nothing). The offer is a card
beside the anchor with the tour's name,
its length and **Show me** / **Not now**; it does not dim the app or move
focus. A **hint** (`hint: true`) is a one-step triggered guide: its card is
the whole thing, with **Got it**.

| Guide | Offered when | Points at |
|---|---|---|
| Citations in answers | an AI reply finishes with a citation link (`chat.cited`) | the link; a demo clicks it and waits for `citation.shown`, then the marked passage in the PDF |
| Sharing a page | the page gets its first share link (`share.created`) | (create the link,) access, people, the link — inside the Share popover |
| Editing tables | an editable table renders in the notes (`table.shown`) | (add one with /table,) the + strips, row/column handles, cells, the corner handle (copy or delete the whole table) |
| Handwriting | the first stroke (`ink.stroke`) | (draw something,) tap the pen again for colour, width and pen vs monoline, erase part of it, undo, the lasso, the ink block in the notes |
| Working together | someone else comes onto the page (`peer.joined`) | the avatar stack, their block, undo |
| Resolving a conflict | a clone conflict's versions show (`conflict.shown`) | the versions, Apply |
| Shared workspaces | the account belongs to a shared workspace (state) | the account menu's switcher and card |
| hint: math keys | the live formula preview comes up (`math.previewed`) | the preview: Tab and `\` |
| hint: block references | the `[[` search shows results (`ref.search`) | the search: mention vs `![[…]]` |
| hint: Ctrl+P | the 4th return to the library in one load (`home.opened`, `count: 4`), unless the palette was used (`doneOn: palette.opened`) | Home |
| hint: folders | the library has 10+ pages and no folder or label (state) | the listing bar |
| hint: install | iPhone/iPad Safari, not yet the home-screen app (state) | no anchor: a corner card |

Rules the engine keeps (`useGuide.js`, `triggers.js`):

- **Trigger shape.** `trigger: { event, match?, count?, doneOn? }`. Without
  `event` it is a state trigger: offered once the tour's `requires` hold,
  checked when the facts change, never in the first 3 s after load. The
  tour-level `requires` also gates manual starts; step-level `requires`
  still filters steps.
- **After the render.** An event is judged after the render it came with,
  so the facts include what the same action changed (`share.created` sees
  the new link). Only events some trigger listens for are queued.
- **One per page load.** At most one automatic offer per load, never while a
  tour runs, never while Settings is open (`guideAvailable`), never in the
  share view. An event that comes while the guide is busy is not queued.
- **Once per version.** Showing an offer records it (`offered`); Not now,
  Esc or ×, Show me and Got it each settle it. Bump `version` only when a
  finished user should be offered it again.
- **Suggest tours.** Settings → Appearance → Tours → **Suggest tours**
  (`suggestTours`, an account preference, default on). Off, nothing is
  offered by itself; the Tours menu still works. Nothing is offered before
  the account's synced profile has loaded, so a browser whose cached value
  is stale cannot slip one in.
- **Where the offer points.** `offerAnchor` (default: the first step's
  anchor) and `offerPlacement`. Offers are never revealed: if the anchor
  leaves (the popover closed, the formula ended), the offer goes and counts
  as dismissed. An anchorless hint is a card in the corner.

Engine abilities available to every step:

- **Reveal by `open` path.** A step whose anchor is registered with
  `open: [...]` is revealed by clicking through that path (skipping the
  parts already open) instead of tidying the app first; every other step
  still gets App's `tidy` (close the topbar popovers). The sharing tour
  stays inside the Share popover this way, the workspaces tour inside the
  account menu.
- **The card is inert to the page.** It never takes focus (mousedown is
  prevented, so an editor keeps its caret) and never counts as a click
  outside the popover it points into (pointer and mouse down stop at it).
- **Steps that have the user make something** (`creates: anchor`). When a
  later step needs something to point at that may not exist — a drawing, a
  table, a share link — the step before it asks the user to make one, with
  `advanceOn` the event that says they did. It is dropped when the tour
  starts if that anchor is already on screen, and passed over when the
  anchor turns up within 1.5 s of the step opening (too soon for the user to
  have made it: the Share popover loads its link after it opens). A tour
  triggered by the thing itself therefore starts right after it.
- **`reveal: anchor`** on a step: a surface to bring up (through that
  anchor's `open` path) while the spotlight points elsewhere — the practice
  steps of handwriting spotlight the whole PDF pane (`pdf.pane`) with the
  tool strip revealed, so the user picks a tool and uses it in one hole.
- **Pass-overs never skip two steps.** The engine's own moves (an optional
  step's pass-over, a `creates` step's, the Done acknowledgment) advance only
  from the step that scheduled them.
- **`optional` steps.** Passed over silently when their anchor is not on
  screen: when the step starts, or when it leaves while showing (the block
  someone else was on, once they go).
- **`pick: "last"`** in the registry for an anchor that repeats where the
  newest one is meant (the latest chat reply's citation).
- **`data-guide-active`** on whatever the guide points at, so a control that
  only shows on hover also shows then (the table's + strips).
- **`show`** on a tour: a surface App brings up before the first step
  (`services.show("chat")`).
- **`Section guide="…"`** in the settings kit groups a section's header and
  rows under one anchor (the Share popover's Access and People).

## Demo steps

A step with `do: [...]` acts on the UI itself instead of asking the user to.
The first tour's second step clicks Add, types the arXiv link of *Attention
Is All You Need*, presses Enter, waits for the page to open and moves on; the
user's first task is then a highlight on a real paper rather than a menu.
If the link identifies a paper with a PDF already in the library, Add opens
that saved copy without resolving or downloading the source again. Reopening
the current paper also emits `page.opened`, so replaying the tour completes.

Before the user's highlight step, `{previewHighlight: true}` demonstrates a
drag across visible, rendered PDF text and points to the real colour palette.
It clears its temporary selection on completion or dismissal, never saving a
highlight or note. It waits for text to render, skips after 15 seconds on a
scan without selectable text, and respects reduced motion. The text-layer
and colour anchors may repeat; the preview chooses a visible passage and the
first colour. The user's own highlight is still what completes the next step.
For the demo paper it prefers the abstract sentence beginning “We propose a
new simple network architecture”, matching across text spans and line breaks
and scrolling the passage into view. Other papers use a visible passage.

The next demo, `{previewArea: true}`, uses the PDF's real Ctrl+pointer-drag
handler to draw a rectangle, with a Ctrl badge beside the animated cursor.
It cancels the drag before release, so it creates no snapshot or annotation.
The user then draws their own rectangle and chooses a colour. The
`highlight.created` event carries `kind: "text" | "area"`, so each practice
step completes only for its matching type.

- Actions: `{click: anchor}`, `{type: anchor, text, speed?}`,
  `{press: "Enter", on?: anchor}`, `{waitFor: {event, match?}, timeout?}`,
  `{wait: ms}`. Click and type move the spotlight and a drawn pointer to the
  element first, pause a beat, then act; typing goes through the native value
  setter plus an `input` event so React-controlled inputs see it; keys are
  dispatched as `KeyboardEvent`s (the engine's own hotkeys ignore untrusted
  events). Events that fire during the step are buffered, so a `waitFor` after
  a fast action still catches its result.
- `{name}` in typed text is filled from the tour's `vars`, which the
  localStorage key `gamma-guide-vars` overrides — how the browser suite points
  the demo at an uploaded PDF instead of the network.
- From the demo's initial pause through its final action, the sheet swallows
  clicks, the card shows "watch", and Back/Next are hidden. Arrow and Enter
  navigation is paused too; the close button and Esc still leave the tour.
  After the actions, a step without `advanceOn` advances by itself, one with
  it hands over to the user. A failed action (anchor never appeared, event
  timed out) leaves the card up with "couldn't finish" and Skip; the tour is
  never stuck.
- `placement` on a step names the card's preferred side so it does not cover
  what the demo is about to open (the Add step sits to the left of the
  button; its popover opens below).

The survey behind these choices is [docs/research/onboarding.md](../research/onboarding.md).

## Goals

1. A new account reaches the "aha" (a highlight that became a note block under
   an open paper) in under two minutes without reading docs.
2. The guide points at the real UI, not at screenshots. What it teaches is
   done in place, and the guide notices when it was done.
3. Moving, renaming or removing a control never silently breaks the guide.
   The failure mode is a named test failure, never a card pointing at nothing.
4. Adding or changing a step is editing data in one file, not React code.
5. Nothing is forced. Every surface is dismissable, everything can be reopened
   from one place, and the share view never shows any of it.

## Three layers

Onboarding is three independent surfaces over one shared engine. Each can be
edited or switched off without the others.

| Layer | What it is | Driven by | Where the content lives |
|---|---|---|---|
| **Welcome page** | A real page in the new workspace ("Welcome to Gamma"), with its own sample PDF attached, so every tour step has something to act on | Seeding | `backend/gamma/onboarding/welcome.md` (imported through the `.md` parser at seed time, not Python tuples) |
| **Tours** | Sequential coach marks: a spotlight on one control plus a card. Task-driven: a step advances when the user does the thing, or on Next | The guide engine | `frontend/src/guide/tours/*.js`, one declarative script per tour |
| **Checklist + hints** | "Getting started" checklist with progress (persists until done or dismissed), plus one-off hints right after first use of a feature | State, not sequence | `frontend/src/guide/checklist.js` (design); hints are built, as one-step triggered guides in `frontend/src/guide/tours/hints.js` |

The order matters for reconfigurability: content (the welcome page) changes
most often and costs nothing to change; tours change when the UI changes and
are protected by tests; the engine changes rarely.

## Anchors: the contract between UI and guide

The one rule that makes the guide survive UI changes: **the guide never
selects by class name, text or DOM position.** It selects by anchor id.

- An anchor is a `data-guide="<id>"` attribute on the element the guide should
  point at. Ids are dotted and named by meaning, not by look:
  `header.attach`, `header.share`, `header.search`, `home.import`,
  `home.newPage`, `page.focusedBlock`, `pdf.textLayer`, `dock.chat`,
  `dock.notes`, `row.handle`, `account.menu`, `settings.ai`.
- `frontend/src/guide/anchors.js` is the registry: every id with a one-line
  description, the view it lives in (`home` / `page` / `pdf` / `any`, which
  the browser suite checks are present; any other view — `chat`, `table`,
  `presence`, `merge`… — names the situation that brings the anchor up and is
  not checked), for anchors inside a menu or popover the `open` path the
  engine clicks first, and `pick: "last"` when the newest of several is
  meant. Adding an attribute in JSX without registering it, or referencing
  an unregistered id from a tour, fails `npm test`; the scan also reads a
  conditional `data-guide={cond ? "id" : undefined}`.
- Dev inspector: `/?guide=inspect` outlines every anchor currently in the DOM
  with its id, the way Figma's inspect overlay labels layers. This is how you
  re-point a tour after a redesign: open the inspector, read the id, edit the
  step.
- Anchors move with the JSX they decorate. When a control is deleted, delete
  its registry row; the tests then name every step that referenced it.

Because App.jsx is still being decomposed ([frontend-refactor.md](frontend-refactor.md)),
anchors are the only thing the guide needs from it. No guide code imports App
state directly; App passes the few facts the engine needs (view mode, whether
the page has a PDF, whether an AI provider is configured) through one
`useGuide()` call.

## Tour scripts

A tour is data. `frontend/src/guide/tours/firstRun.js`:

```js
export default {
  id: "first-run",
  version: 2,                 // bump to re-offer the tour to everyone who finished v1
  title: "Your first paper",
  estimate: "2 min",
  when: { signedIn: true, shareMode: false, readOnly: false },
  steps: [
    {
      id: "open-welcome",
      anchor: "home.card.welcome",   // the seeded page's card
      title: "Every paper is a page",
      body: "Notes and highlights live on the page that carries the PDF. Open the welcome paper.",
      advanceOn: { event: "page.opened", match: { seeded: "welcome" } },
      fallback: { action: "navigate", to: { page: "welcome" } },  // Next does it for you
    },
    {
      id: "highlight",
      anchor: "pdf.textLayer",
      placement: "left",
      title: "Select text to highlight it",
      body: "Drag over a sentence in the PDF. The highlight becomes a block in your notes.",
      advanceOn: { event: "highlight.created" },
      requires: { view: "page", hasPdf: true },
    },
    {
      id: "outline",
      anchor: "page.focusedBlock",
      title: "Notes are an outline",
      body: "Enter makes a sibling, Tab indents, Shift+Tab outdents.",
      advanceOn: { event: "block.indented" },
    },
    { id: "search", anchor: "header.search", body: "Ctrl+F searches this paper's notes, the PDF text and the whole library.", advanceOn: { event: "search.opened" } },
    { id: "chat", anchor: "dock.chat", requires: { aiConfigured: true }, skipWhenUnmet: true, body: "Ask about the open paper. Answers cite pages you can click.", advanceOn: { event: "chat.sent" } },
    { id: "chat-setup", anchor: "dock.chat", requires: { aiConfigured: false }, skipWhenUnmet: true, body: "Chat needs an AI key. Add one under Settings → AI.", advanceOn: { event: "settings.opened", match: { pane: "ai" } } },
    { id: "share", anchor: "header.share", body: "Share the page as a link. Viewers see your highlights; editors can add their own.", advanceOn: { event: "share.opened" } },
  ],
};
```

Step fields:

- `anchor` (required), `placement` (auto by default; the positioner flips to
  fit), `title`, `body` (markdown, rendered by the app's markdown component so
  kbd marks and links work).
- `requires`: facts that must hold for the step to make sense. With
  `skipWhenUnmet` the step is dropped from this run; without it the engine
  shows a **detour card** instead: "This step needs an open paper", pointing at
  the anchor that gets you there (`fallback.action`). A step never blocks.
- `advanceOn`: an event from the catalog below, optionally with a `match` on
  its payload. Next always advances too; task-driven advancement is a
  convenience, not a gate. `advanceOn: null` means Next only.
- `open`: a path the engine performs before showing the step when the anchor
  is inside a closed surface, e.g. `["account.menu"]` clicks the account menu
  so `account.workspaces` becomes visible. Declared per anchor in the registry,
  overridable per step.

## The event catalog

The app emits a small number of named events; tours advance on them and
triggered tours are offered from them. `frontend/src/guide/events.js` exports
`guideEvents.emit(name, payload)` and the catalog, each emitted at one point
where the thing happens:

| Event | Emitted by |
|---|---|
| `popover.opened` `{name}` | App, when a topbar popover opens |
| `page.opened` `{id}`, `home.opened` | App's page open and `goHome` |
| `palette.opened` | App, when the Ctrl+P palette opens |
| `highlight.created` `{id, kind}` | App's highlight creation path |
| `chat.sent`, `chat.cited` | ChatDock's send, and the end of a reply holding a citation (`gammaLinksIn`) |
| `citation.shown` | PdfCitationOverlay, once a quote is found and marked |
| `share.created` | App's `createShareLink` |
| `peer.joined` | App, when someone else appears on the open page |
| `ink.stroke` | App's `handleInkStroke` |
| `table.shown` | MdTableWrap, when an editable table mounts |
| `conflict.shown` | MergeResolver's versions |
| `ref.search` | BlockTree, when the `[[` search shows results |
| `math.previewed` | MathLivePreview, when it comes up |
| `block.created`, `block.indented`, `settings.opened` | catalogued, not emitted yet |

A test asserts every `advanceOn`, trigger and `doneOn` name is in the catalog.

Events are the seam that keeps tours out of App.jsx: instrumenting a new
event is one line at the point where the thing happens, and every future tour
can use it.

## The engine

`frontend/src/guide/useGuide.js` + `GuideOverlay.jsx`, mounted once in App.

State machine: `idle` → `offered` (the small invitation card) → `running
{tourId, stepIndex}` ↔ `waiting` (anchor not in the DOM yet) → `done` /
`dismissed`.

- **Finding the anchor**: `document.querySelector('[data-guide="id"]')`,
  retried on a `MutationObserver` for up to 3 s (the page may still be loading:
  the PDF skeleton, a lazily mounted dock). If it never appears the step is
  reported (`guideEvents.emit("guide.anchorMissing")` plus a `console.warn`)
  and the tour skips it rather than showing a card in the void. The e2e suite
  already fails on console errors; the scenario promotes this warning too.
- **Spotlight**: one fixed overlay with an SVG mask cut out around the anchor's
  rect (the Driver.js shape). The cutout passes pointer events through so the
  user acts on the real control; the dimmed area swallows clicks. Repositioned
  on scroll and resize via `ResizeObserver` + `requestAnimationFrame`; the
  anchor is scrolled into view first.
- **Card**: the app's popover look (same tokens as the account menu and the
  Share popover, per [ui-design.md](ui-design.md)): title, body, `step n of
  m`, Back / Next / Skip tour. On phone and iPad widths the card becomes a
  bottom sheet and the spotlight stays; touch targets follow [ipad.md](ipad.md).
  Esc dismisses. Focus stays where the user is working; the card is
  `aria-live="polite"`, never a focus trap.
- **No new dependency.** The positioner is about sixty lines (measure rect,
  pick side, clamp to viewport). The app already positions popovers without a
  library.

## Starting and replaying

Open **Account > Tours** and choose a tour; the menu lists the ones that can
start here (see Manual tours), and every tour can be replayed. Triggered tours
and hints also come by themselves, once each (see above), unless Suggest
tours is off. AI chat opens its dock if needed; PDF-specific steps are
included only when the PDF is visible. Share views do not mount tours.

## Storage

One synced, account-wide pref `onboarding` (add it to `db.USER_PREF_KEYS`;
no schema step, the prefs KV already exists):

```json
{ "tours": { "first-run": { "version": 2, "step": 4, "state": "running" } },
  "checklist": { "highlight": "2026-09-18T10:00:00Z", "share": null },
  "hints": { "chat-empty": true },
  "dismissedAt": null }
```

localStorage (`gamma-onboarding:<user>`) is the instant-paint cache, server
wins, same rule as `appearance` ([settings.md](settings.md)). No per-workspace
scope: you learn the app once, not once per workspace.

## The welcome page and its sample PDF

- Content is `backend/gamma/onboarding/welcome.md`, a normal markdown outline,
  imported at seed time with the same parser as `POST /pages/from-file`, so
  what the importer supports the welcome page supports (callouts, math, images,
  tables). Editing copy is editing markdown.
- The page carries a PDF so "select text to highlight" has a target. The PDF is
  **rendered from the same markdown by the notes-as-PDF writer**
  (`pdf_notes.py`, [import_export.md](import_export.md)) at seed time and
  stored through the content-hash store like any upload. No binary asset in the
  repo, no licence question, always in step with the text, and it demonstrates
  the export feature. Images come from `frontend/public/media/` served by the
  same origin, not GitHub raw.
- Seeded into every new personal workspace's first creation
  (`workspaces.ensure_personal(..., welcome=True)` for a new account, in
  addition to the guest). Shared workspaces get nothing. The page has
  `properties.seeded: "welcome"` so tours can find its card
  (`home.card.welcome` is derived from that property) and so a re-seed can
  tell it apart from user pages. Deleting it is fine; the tour's detour card
  then says "Open any paper".
- The seeded blocks go through `commit_ops` like every other writer
  ([collab.md](collab.md)); the current raw-insert path in `seed.py` is
  replaced, not extended.

## Checklist and hints

- The checklist (`checklist.js`) is five items, each `{id, label, doneOn:
  event, tour: {id, step}}`: import or open a paper, make a highlight, indent a
  block, search, share. Completion is recorded from the same event bus, so
  doing the thing on your own counts even if you never ran the tour (the
  Linear pattern). Progress shows as a thin bar in the popover and a dot on the
  account button until done or dismissed.
- Hints are built: one-step triggered guides in `tours/hints.js` (see
  Triggered tours and hints). The chat with no AI provider gets no hint: its
  empty state already says "Connect an AI provider to start". Empty states in
  the library and the notes column stay where they are; hints point at
  controls, empty states explain areas.

## Files

```
frontend/src/guide/
  anchors.js        registry: id → {description, view, open?, pick?}
  events.js         guideEvents bus + the catalog
  triggers.js       when a tour is offered; per-account progress
  useGuide.js       state machine, offers, reveal, demo actions
  GuideOverlay.jsx  spotlight + card + bottom sheet, offer and hint cards
  checklist.js      items and their events (design)
  tours/*.js        one file per tour; tours/index.js registers them
  tours/hints.js    the hints
  guide.css
backend/gamma/onboarding/welcome.md   the seeded page (and the sample PDF's source)
backend/gamma/seed.py                 imports it; renders the PDF; commit_ops
frontend/tests/guide.test.mjs         schema, anchor references, event names, unique ids
frontend/tests/e2e/scenarios/guide.mjs            the first-run tour end to end, home anchors present
frontend/tests/e2e/scenarios/contextualGuide.mjs  the AI chat tour on desktop and phone
frontend/tests/e2e/scenarios/triggeredGuide.mjs   offers and hints: tables, sharing (offered, and from
                                                  the menu with and without a link), citations, math,
                                                  Ctrl+P, workspaces, presence, handwriting from the
                                                  menu (draw, style, erase, undo), Suggest tours off
```

## Validation

- `npm test`: every tour parses against the step schema; every `anchor` is in
  the registry; every `advanceOn`, trigger and `doneOn` event is in the
  catalog; a hint is one step; ids are unique; `version` is an integer; the
  trigger rules (event, `count`, `doneOn`, state, version) behave.
- `npm run e2e -- --only "triggered guide"`: each offer appears after its
  event without dimming the app; Show me runs the tour (inside the Share
  popover and the account menu without closing them); a hover-only control
  shows while pointed at; a hint keeps the editor's caret; an optional step
  whose anchor left passes without a warning; nothing is offered twice, or
  at all once Suggest tours is off in Settings.
- `npm run e2e` (`guide.mjs`): for each view (`home`, `page`, `pdf`) every
  registry anchor declared for that view is in the DOM (after performing its
  `open` path); the first-run tour is driven end to end by performing each
  step's action and asserting the card moved to the next anchor; the detour
  card shows when a step's requirement is unmet; `?guide=` starts a tour; the
  share view mounts nothing; any `guide.anchorMissing` warning fails the run.
- Backend: seeding a personal workspace produces the welcome page with an
  attached PDF and a `seeded` property; the tuple-free seed path goes through
  `commit_ops`; a guest re-seed is idempotent.

## Build order

1. Anchors + registry + inspector + the node test. Decorate the roughly
   fifteen controls the first tour needs. No visible change.
2. Event bus + the ten emit points.
3. Engine + overlay, driven by a hard-coded two-step tour behind `?guide=`.
4. `firstRun.js` in full, the `onboarding` pref, the invitation card and the
   account-menu entry. E2e scenario.
5. Welcome page as markdown + rendered sample PDF, seeded for new accounts.
6. Checklist popover, then hints.

Steps 1 to 3 are invisible to users and safe to merge one at a time.

## Rules once this exists

- New control worth teaching: add `data-guide`, register it, done. Never point
  a step at a class.
- New feature worth a triggered tour: emit an event where it happens (one
  line, catalogued), write `tours/<name>.js` with a `trigger`, register it in
  `tours/index.js`, add its strings to the catalog. A single card is a hint.
- Renaming or removing a control: update the registry row; the tests name the
  affected steps.
- Copy changes: edit the tour file or `welcome.md`. Bump `version` only when a
  finished user should see the tour again.
- Keep this doc, [settings.md](settings.md) (the `onboarding` pref) and
  [api.md](api.md) (the prefs key list) in step.
