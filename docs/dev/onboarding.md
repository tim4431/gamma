# Onboarding: tours and contextual guides

**Status: the engine, the two manual tours (first paper, AI chat), thirteen
triggered tours, twelve hints and the welcome page with its sample PDF are
built. The first paper tour is also offered to every new library.** A synced
`onboarding` pref and a checklist are not built (see "Not built").
What exists: `frontend/src/guide/` (anchor registry, event bus, trigger rules
in `triggers.js`, `useGuide`, `GuideOverlay` and its positioner `place.js`,
the scene player `scene.js`, the illustration registry `media.js` with its
drawings in `media/` and their shared stylesheet `media.css`, one file per
tour in `tours/`, the hints in `tours/hints.js`), the node test
`tests/guide.test.mjs` and the e2e scenarios `guide.mjs` (first paper),
`contextualGuide.mjs` (AI chat) and `triggeredGuide.mjs` (offers and hints).

The first tour is a welcome card and five steps. Gamma adds a paper (the
add-a-paper demo). It drags across a sentence, and the user may highlight
one of their own; then the same for a box around the equation. One demo
types a note and adds the `llm` label. The last step is a spotlight on the
Home button (on a phone, the bottom bar's Library tab, which carries
`header.home` there). Each highlight step keeps Next after its demo, and
the user's own highlight of that kind ticks it. Returning to the library emits `home.opened`, shows
Done, and completes the tour automatically; Finish can also close it.
Either way the tour ends on its finish card (below).

The welcome card (`welcome: true` on the tour, its first step `intro:
true`) is a centred 440 px card. It holds a "2-minute tour" chip
(`minutes`), the title, one sentence saying Gamma shows each move once and
then the user tries it, and a three-line `outline` marking each line
*watch* or *you try*. **Explore on my own** (`later`) dismisses it, **Start
the tour** (`next`) starts it, and a footnote says the tour can be replayed
from the account menu. A demo server gets its own title ("Welcome to the
Gamma demo"): two `intro` variants, picked by `requires: {demo}`. The card
is not counted as a step (the add demo reads "Step 1 of 5"). It is also the
tour's offer, which every new account gets on arrival (see the table below)
without anything dimmed. Start begins with the add demo; Explore on my own
settles the offer for the tour's version.

The finish card (`finishCard` on the tour, `guide/finish.js`) comes up when
the last step completes. It is centred over a light scrim, with a beacon on
the account button. Under "That's your first paper" it lists what the run
made, each line only when it happened:

- the paper: the run's first `page.opened`, whose payload carries its `title`;
- the number of highlights (`highlight.created`);
- the note and the `llm` label, when their demo ran to the end.

Two tiles follow. **Ask the paper** starts the AI chat tour when an AI
provider is connected, else opens Settings → Connections (App's
`openSettings` service). **More tours** opens the account menu's Tours
list. Then the footnote "Replay any tour from the account menu" and
**Done**. "done" is recorded when the card opens, and closing it (Done, ×,
Esc, the scrim, a tile) keeps it. Only a tour with a `finishCard` ends this
way; the others close after their last step.

Progress is a localStorage key per account (`gamma-guide:<account>:<tourId>`;
the first tour keeps its older `gamma-guide:first-run`; see "Storage"). On a
demo server (`facts.demo`, from `GET /api/server-config`'s
`demo`) the same keys live in sessionStorage (`guideStorage(demo)` in
`triggers.js`), so every visit starts fresh ([guests.md](guests.md) "Demo
mode"). Every new account starts with the Welcome page and its sample PDF
(see "The welcome page and its sample PDF").

## Manual tours (implemented)

The account menu's **Tours** submenu lists the tours that can start where the
user is (`guide.startable()`): a tour's `requires` hold and its first step's
anchor, or the control that reveals it, is on screen. A tour with `show`
brings up its own surface first (AI chat opens the chat). So **Your first
paper** is always listed, and so is **AI chat** once `/api/ai/models` has
answered, except for a guest with no AI connected (no step applies there).
**Adding to your library** and **Organize your library** are listed
wherever you can write, the first from its own popover. **Sharing a page**
is listed on
any open page, **Editing tables** on any page you can edit,
**Handwriting** on any PDF you can edit and **Pages to write on** on any
page you can edit; **The PDF viewer** on a PDF whose viewer is open and
**The notebook view** in the notebook view. Each of the first four begins by having you make the
thing it explains when there is none yet (see "Steps that have the user
make something"). **Background tasks** is listed on the desktop layout.
**Shared workspaces** is listed once you belong to one.
**Working together** and **Citations in answers** are listed only while
their subject is on screen: someone else on the page, a cited reply. Hints
are never listed. `?guide=` URLs never start a tour, and the chat header has
no guide button. In Chinese a tour is 教程.

Every card has a title of about six words and at most one short body
sentence. `bodyTouch` words it for a touch screen ("Long-press a word…").
The scene or the demo shows the rest ("Showing, not telling", below). Its
head says what kind of step it is: **▶ Watch** (a demo), **Your turn** (a
step that waits, because later steps need its result), **✓ Done** (the
acknowledgement) or nothing (a light step). "Step n of m" sits on the
right, and a segmented bar shows the progress. A light step has Back and
Next whether or not the user tries what it shows. A demo has **Skip this
demo**; on a demo that then hands over, Skip stops the demo and leaves the
user on the step to try it. A waiting step has Back and a quiet **Skip
step**, so the task, not skipping it, is the loudest thing on the card.

AI chat (`tours/aiChat.js`) has steps per place, chosen by step-level
`requires`, each ending on Send: a light step on the composer whose Done
leaves, and which the user's own send (`chat.sent`) ticks. The tour never
submits a message or starts recording, and there is no voice step.

- **On a paper** (`view: "pdf"`): the message box types *What is the main
  result, and where is it shown?* ("Ask about this paper"); with the PDF
  window visible, the Ctrl-drag demo frames a figure (`findFigure`, below)
  and releases, so the snapshot lands in the chat. The same demo then
  points at it (`{point: "chat.imageContext"}`). Then Send ("Answers link to
  the passages they quote"). Hidden PDFs and phone chat skip the figure step.
- **On a page of notes** (`view: "page"`): *Turn these notes into a short
  summary* ("Or select part of a note to have it rewritten"), then Send.
- **On the library** (`view: "home"`): *Which of these papers use attention?
  File them into ML/attention* ("It can search, read and file your pages" —
  true under the default tool permissions), then the Tools button
  (optional), then Send.
- **No AI connected** (`aiConfigured: false`, and `aiEditable`: the account
  can store keys — a guest cannot, and gets no tour): one step on the chat's
  setup card (`chat.setup`), "Connect an AI to use chat". Clicking a tile
  opens Settings → Connections, whose `settings.opened {pane: "ai"}` ticks
  it. The message box is disabled then, so nothing is typed into it.

`aiConfigured` is unknown (undefined) until `/api/ai/models` answers, so
neither variant is picked too early. Existing drafts are restored after an
example; an empty composer keeps the example ready to edit. When the tour
ends without a send, the figure demo's snapshot leaves the chat again (the
engine's tour-end cleanups, below).

The paper tour frames the actual attention equation, types a note through
CodeMirror without replacing existing writing, adds the `llm` label through
the normal label field, and ends at Home. Successful user actions briefly
show Done before advancing automatically.

## Triggered tours and hints (implemented)

**Arrange windows** (`tours/windows.js`) is available under Account → Tours
on desktop, including without an AI connection. It also offers once after
opening a PDF (`page.opened`). Starting it reveals and expands Chat and,
when the PDF is visible, Notes. It is two light steps on Chat's title, each
with a scene. In "Double-click to fold, again to unfold" the ghost pointer
double-clicks the real title, and `window.collapsed {id: "chat"}` ticks the
step. In "Drag a title to move the window" the pointer carries the title to
the left, where the dock's own drop preview shows where Chat would land;
`window.moved {id: "chat"}` ticks it. The user's arrangement is
kept. Phone layouts have no dock controls and do not list or offer this
tour.

**The PDF viewer** and **The notebook view** (`tours/viewer.js`) walk the
viewer's left edge top to bottom: the PDF's table of contents (`optional`:
only a PDF with an outline has it), then the tool column. The two viewers
share the column up to the pen (zoom out, zoom in, fit to width, the pen,
in the same places in both). The zoom step's scene visits the three zoom
buttons; the pen step's presses the pen and writes a stroke on the page.
What only one viewer has comes after them: Translate and the phone's
selection mode on a PDF, Paper and Notes view in the notebook view. So the
tours are **siblings**: the zoom and pen steps are `shared`, the same step
objects in both files, and a user who has finished one tour gets the other
without them. After the PDF tour the notebook view's is two cards (Paper,
Notes view). The other way round, the PDF's tour keeps only the outline
and Translate. When the paper has neither, the tour is neither offered nor
listed (a tour with nothing to show, below). "Finished" is the
sibling's stored `done`; a tour left halfway teaches nothing to skip.

The PDF viewer is offered on `page.opened`. It is listed after Arrange
windows, which therefore takes the first paper of a load; a paper opened on
a later visit gets this one. The phone has no windows tour, so it gets this
one first. The notebook view is offered once the user is in it (state).
Both require `viewerTools`: the column is on screen, not closed, and on a
phone not under the Notes or Chat panel. The notebook view's last card
points at Notes view, the button that leaves it. Clicking it ends the tour
as done (the engine's rule for leaving on a last card, below).

A tour with a `trigger` is also offered by itself, once per `version`, right
**after** the thing it explains happened, never on mere contact with a
control (focusing the chat composer offers nothing). The offer is a card
beside the anchor. It reads "Quick tour · N steps", then what just happened
and why the tour is worth it: the tour's `offer: { title, line }` ("You
made a table" / "Cells, rows and columns are edited in place."), else its
name. Its buttons are **Show me** and **Not now**; the Tours menu keeps the
plain name. It does not dim the app or move focus. A beak on the card's
edge points at the control, and a beacon marks it: an accent ring and halo
that pulse twice, then stay, with clicks passing through. A **hint**
(`hint: true`) is a one-step triggered guide whose card is the whole thing:
an amber **Tip** chip over one plain sentence that says why it helps, with
**Got it**.

| Guide | Offered when | Points at |
|---|---|---|
| Adding to your library | Add is opened (`popover.opened {name: "add"}`). The only guide that comes *with* its control rather than after it: opening Add is already the intent, and the rows it explains are on screen only while the popover is | inside the popover, undimmed whole: the address box (a scene typing into it), Upload files, New notebook (a drawing of the one page behind both views) |
| Arrange windows | a PDF is opened (`page.opened`) on desktop | Chat's title: a scene double-clicking it to fold, then one dragging it to the left dock |
| Citations in answers | an AI reply finishes with a citation link (`chat.cited`) | the link; a demo clicks it and waits for `citation.shown`, then the marked passage in the PDF |
| Sharing a page | the page gets its first share link (`share.created`: the first audience tile picked or person invited) | (choose who can open it,) the link, who has access, general access with Stop sharing (worded for what an anyone-with-the-link share exposes when it is one, `shareAudience`) — inside the Share popover, top to bottom, the popover undimmed whole |
| Editing tables | the user makes a table (/table, or a pasted spreadsheet or html table) and it first renders (`table.created`); opening a page that has one offers nothing | (add one with /table,) a cell to type into, the + strips and the row/column handles, the corner handle (copy, move or delete the whole table), each with a scene clicking it — on the table just made when the page has several, each card clear of the table |
| Handwriting | the first stroke (`ink.stroke`) | (draw something, with a scene writing on the page,) the drawing's note block and its caption, tap the pen again for colour, width and pen vs monoline, then the eraser and the lasso on one card (Ctrl+Z brings back what was erased); finishing re-arms the pen |
| The PDF viewer | a PDF is opened (`page.opened`) with its viewer on screen (`viewerTools`), in a load whose offer Arrange windows did not take | the table of contents (if any), zoom and fit, the pen, Translate (if on), the phone's selection mode; after The notebook view, only the outline and Translate |
| The notebook view | the notebook view is open (`notebookView`, state) | zoom and fit, the pen, the paper, Notes view (a drawing of the one page behind both views); after The PDF viewer, the paper and Notes view |
| Pages to write on | a sheet of paper is made (`sheet.created`: a new notebook, **+**, `/note`, or writing low on the last sheet), in the notes view (`notebookView: false`: the notebook view has none of the steps' anchors) | (make one,) the sheet (a scene writing low on it while the next page's outline arrives under it), its pen, the notebook view (a drawing of the one page behind both views). The paper is the notebook view tour's to teach |
| Background tasks | the account starts a job on the server (`job.started`, from the tasks store's `adopt`, so every kind counts); desktop only — the phone's tray row carries no anchor | the tray button (the job runs on the server and outlives the tab); a task's row with Clear finished, `optional`: a tour started from the menu may find no rows |
| Organize your library | the library passes 20 pages (`growingLibrary`, state) — past the folders hint, which arrives at ten and only says how to move one page | a page card (a scene right-clicking it), a centred card with a drawing of one paper in several folders, Recently viewed, Recently deleted; the last two are `optional`, so a library with nothing viewed passes over them |
| Working together | another account or a link visitor comes onto the page (`peer.joined`; your own second tab or the desktop app beside the browser is nobody else) | the avatar stack (a face marks their block), undo |
| Shared workspaces | the account belongs to a shared workspace (state) | the account menu's switcher and card |
| hint: math keys | the live formula preview comes up (`math.previewed`) | the preview: Tab and `\` |
| hint: block references | the `[[` search shows results (`ref.search`) | the search: mention vs `![[…]]` |
| hint: Ctrl+P | the 4th return to the library in one load (`home.opened`, `count: 4`), unless the palette was used (`doneOn: palette.opened`); never on the phone layout (`requires: {phone: false}` — App's `phone` fact), where Home is the bottom bar's Library tab | Home |
| hint: Back | the first link jump (`nav.pushed`), unless Back was already used (`doneOn: nav.back`) | the Back button: it returns to the same spot, or `{key:app.back}` |
| hint: conflict | a clone conflict's versions show (`conflict.shown`) | the versions: nothing was lost, both are kept; pick one, then Apply |
| hint: folders | the library has 10+ pages and no folder or label (state) | the listing bar |
| hint: install | iPhone/iPad Safari, not yet the home-screen app (state) | no anchor: a bottom-left card |
| hint: approvals | the assistant asks before a change (`approval.shown`, from `ApprovalCard`) | the card: allow once or for the whole chat, and that Don't allow can say what to do instead |
| hint: export | the View menu opens (`popover.opened {name: "menu"}`) on a page with 5+ highlights (`annotatedPage`) — the Export row is on screen only while that menu is | Export…: the PDF with the highlights drawn on it, or Markdown |
| hint: clone sync | the workspace has an offline copy or a publication (`clonedWorkspace`, state) | the sync pill: what is still to go, and its log |
| hint: Gamma Connector | the first paper fetched from an address (`paper.fetched`, from App's `openPdf`) in a browser with no Connector (`connectorHere: false` — `shared/lib/connector.js` asks it through the extension's `bridge.js`; unknown in the desktop app, whose Connector would be in the system browser, so nothing is suggested there) | Add: what the extension does for papers a publisher won't hand over, and where to get it |
| hint: Gamma Cloud | a setting that travels with the account was changed here (`prefsChanged`: the first push after the profile sync has been quiet once, so a profile arriving or seeding itself does not count) on a server offering cloud sign-in to an account that has not linked one (`cloudLinkable`, from `GET /api/auth/cloud/status`) — state, so the card comes once Settings is closed again | the account button: linking carries these settings to the user's other Gamma servers |
| Your first paper | on the library of a demo server once the guest lands, or of a library with nothing in it yet but the seeded Welcome page (`emptyLibrary`: the listing has loaded and every root page has `properties.seeded`) — state, `trigger: {requires: {view: "home", editable: true}, anyOf: [{demo: true}, {emptyLibrary: true}]}`; not once the tour has any progress | no anchor: the centred welcome card; Start the tour begins with the add demo |

Rules the engine keeps (`useGuide.js`, `triggers.js`):

- **Trigger shape.** `trigger: { event, match?, count?, doneOn?, requires?,
  anyOf? }`. Without `event` it is a state trigger: offered once the tour's
  `requires` (and the trigger's) hold, checked when the facts change, never
  in the first 3 s after load. The tour-level `requires` also gates manual
  starts; the trigger's `requires` gates only the offer, which is how the
  first-run tour is offered on the library while staying startable from
  the Tours menu everywhere. `anyOf` is a list of fact sets of which at
  least one must hold as well (a demo server, or an empty library).
  Step-level `requires` filters steps when the tour starts, so two steps
  can be variants of one another (the sharing tour's access step for
  `shareAudience: "anyone"` and for the rest). A required value that is an
  array means one of its values.
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
  as dismissed. An anchorless hint is a card in the bottom-left corner,
  clear of the chat composer; on the phone layout every card is a sheet
  above the bottom tab bar.

Engine abilities available to every step:

- **Tour-end cleanups.** A demo action gets `services.onTourEnd(fn)` next to
  its step's `onCleanup`: what it leaves for the tour's later steps (the AI
  chat tour's snapshot) is taken back when the tour ends, however it ends.
- **Reveal by `open` path.** A step whose anchor is registered with
  `open: [...]` is revealed by clicking through that path (skipping the
  parts already open) instead of tidying the app first; every other step
  gets App's `tidy` (close the topbar popovers). The sharing tour
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
  someone else was on, once they go). An optional first step with nothing
  to point at does not keep a tour out of the Tours menu: the step after it
  decides (a PDF without a table of contents).
- **Sibling tours** (`sibling: "<tour id>"` on both), for two tours about
  controls that are partly the same. A step marked `shared` is left out of
  the run once the sibling is `done` (`stepApplies` in `triggers.js`). The
  viewer tours above.
- **A tour with nothing to show** is neither listed nor offered: when every
  step left after `requires`, `creates` and `shared` is `optional` and none
  of their anchors is on screen (`firstShown` in `useGuide.js`).
- **Waiting steps.** A `creates` step waits for the user (Your turn, no
  Next), because later steps need what it makes. Every other step is light.
- **Leaving on the last card.** When a run's prerequisites stop holding, it
  ends. If it was on its last step and that step waits for no action, the
  tour is recorded as done, since every card was shown.
- **`pick`** in the registry for an anchor that repeats: `"last"` where the
  newest one is meant (the latest chat reply's citation), `"recent"` where
  it is the one inside the element marked `data-guide-recent` (the table
  the user just made, not an older one above it).
- **`avoid: anchor`** on a step: a box the card keeps clear of besides the
  spotlight. The tables tour's add strip sits under the table, so with no
  room below the card goes above the whole table, else beside it, never
  flipped onto it (`guide/place.js`).
- **`data-guide-active`** on whatever the guide points at, so a control that
  only shows on hover also shows then (the table's + strips).
- **`show`** on a tour: a surface App brings up before the first step
  (`services.show("chat")`).
- **`restore`** on a tour: what App restores once its last step is done
  (`services.restore("pen")`: the handwriting tour ends with the lasso or
  the eraser armed, so App re-arms the pen last drawn with). Leaving a tour
  early restores nothing. It is unrelated to `finishCard`, the card above,
  which `useGuide` hands the overlay as `finishCard` with `finishAction` for
  its tiles.
- **`Section guide="…"`** in the settings kit groups a section's header and
  rows under one anchor (the Share popover's Who has access and General
  access; its Link section carries `share.link` only once a link exists,
  which is what the tour's create step waits for).
- **Keys in copy** (`guide/keys.js`). Titles and bodies render `**bold**`,
  `*italic*`, `` `code` `` and key tokens. `{key:app.quickOpen}` names a
  command of the catalog ([hotkeys.md](hotkeys.md)) and shows the chord it
  answers to for this account (rebound in Settings → Keyboard, ⌘ only on a
  Mac) as the settings kit's key caps (`shared/ui/KeyCaps.jsx`). An unbound
  command shows its palette name in quotes. `{key:Tab}` names a fixed key.
  Never type a chord into copy. App passes the `keybindings` preference to
  the overlay; the token stays literal in the i18n key
  ([i18n.md](i18n.md)), and a test checks every token names a command or a
  key.

## Showing, not telling

The October 2026 review set four rules for every tour. Each card is cheap
to try, and the screen does the explaining.

1. **Light by default.** A step that invites a try keeps Next. If the user
   does the thing, its `advanceOn` event ticks it (✓ Done) and the tour
   moves on; if not, Next moves on all the same. Only a step whose result
   later steps need waits for the user: a `creates` step. That card says
   **Your turn**, has no Next, and offers a quiet **Skip step**.
2. **Show on the real UI.** A gesture is a `scene` played over the real
   control: a ghost pointer double-clicks the actual title, drags the
   actual window to where it would dock. It is never a drawing of the
   control beside the control. A drawing (`media`) is only for a concept
   with no place on screen (Illustrations, below).
3. **One sentence per card.** A title of about six words, imperative when
   it is an action. A body only when the title cannot carry it, and then
   one short sentence. The card never narrates what the scene or the demo
   already shows.
4. **One motion system.** One vocabulary of scene primitives, one timing
   scale (`MOTION` in `guide/scene.js`), one layer order, theme tokens only.

### Scenes

A step's `scene: [...]` is a list of primitives `guide/scene.js` plays in
order, then holds the last frame, fades and plays again. It changes
nothing: no click, key or drag reaches the app, and its shapes take no
pointer events. Each primitive names a registered anchor and reads that
anchor's box when it plays, so a scene follows the layout. `at` is a point
in the anchor's box as fractions of it, `[0.5, 0.5]` by default.

| Primitive | What it shows |
|---|---|
| `{point: anchor, at?}` | the pointer glides there and a soft halo marks the spot |
| `{click: anchor, at?, count?, button?}` | a press and its ripple; `count: 2` a double-click, `button: "right"` a right-click (a mouse badge beside the pointer) |
| `{drag: anchor, at?, to: {zone, window}}` | the pointer presses, carries an outline of the anchor and lets go where `window` (`chat` or `notes`) would dock on side `zone` (`left`, `right` or `bottom`). The dock's own drop preview appears there at the size that window would get (App's `dockZone` service, the geometry the real drag previews) |
| `{stroke: anchor, at?: [x0, y0, x1, y1]}` | the pointer writes a squiggle inside that part of the anchor |
| `{type: anchor}` | the pointer steps aside, and a caret runs a line of text into the field |
| `{ghost: anchor, size?}` | a dashed outline of what is about to appear under the anchor (the next sheet of paper) |
| `{wait: ms}` | a pause |

A scene plays only while its step is the user's to look at: not during a
demo or the Done moment, never on an offer or a hint. When the user presses
anywhere, or their pointer comes onto a spot the scene uses (the title it
double-clicks, the part of the page it writes on), the scene clears away,
since they are trying it. It starts again from the top once their
pointer has been elsewhere for 1.5 s. Under reduced motion it plays once
at no speed and keeps its last frame, still. On a touch screen the pointer
is a fingertip and a right-click plays as a long press. The test checks that every
primitive is a known kind and names a registered anchor, and that hints
have no scene.

### Surfaces

An anchor inside a popover or a panel names it as its `surface` in the
registry (`add.popover`, `share.popover`, `account.menu`, `tasks.panel`).
The spotlight's hole is then the whole surface, so the rows around the
anchor stay readable and clickable, and the ring marks the anchor itself.
The card keeps clear of the surface (it joins the step's `avoid` box). A
surface opens by the same `open` path as the anchors in it; the test
checks that.

### Motion and layers

- **Timing.** `MOTION` in `guide/scene.js` is the guide's one scale. A
  glide takes 520 ms (the demo pointer's CSS transition is the same), a
  press 90 ms and a ripple 520 ms. The finished frame holds 1.6 s, then a
  short rest comes before the next loop. Fades use 160 ms, the app's
  `--dur-base`.
- **Layers.** Everything the guide draws lives in one root at
  `--z-tip`, in document order and without z-indexes of its own: the
  dimmed sheet and its ring, the demo's click shield, an offer's beacon,
  the scene, the demo pointer, the card. A scene's shapes always pass
  under the pointer, and the pointer under the card.
- **One pointer.** Scenes and demos draw the same white system pointer
  (`.guidePointer`, `POINTER_PATH`). While a demo or a scene types, the
  pointer fades out, so the text is the thing to watch.
- **The sheet lifts while the user acts.** A press inside the hole lowers
  the dimming for as long as the pointer is down. What the app shows in
  answer is then not dimmed under it: a dock's drop preview, the colour
  palette after a selection.
- **Colours.** Scene shapes are accent outlines and washes from the theme's
  tokens, and the drop preview is the dock's own (`.dockPreview`'s look),
  so a scene never passes for a control of the app.

## Demo steps

A step with `do: [...]` acts on the UI itself instead of asking the user to.
The first tour's second step clicks Add, types the address of the demo paper,
presses Enter, waits for the page to open and moves on; the user's first task
is then a highlight on a real paper rather than a menu. The demo paper is the
seeded Welcome page's own PDF (`/api/uploads/<hash>.pdf`, App's `welcomePdf`
fact) while the library has that page, so the tour needs no network; without
it, the arXiv link of *Attention Is All You Need*.
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
and scrolling the passage into view; it keeps looking for it for 2 s after
the first text renders (its page may still be rendering) before settling
for any visible passage, as other papers do.

The next demo, `{previewArea: true}`, uses the PDF's real Ctrl+pointer-drag
handler to draw a rectangle, with a Ctrl badge beside the animated cursor,
around the attention formula App's `findEquation` service finds. It cancels
the drag before release, so it creates no snapshot or annotation. With
`context: true` (the AI chat tour) it aims at a figure instead: App's
`findFigure` finds the first "Figure N:" caption, and the box is the gap in
the running text above it, labels included. That drag is released, so the
snapshot goes to the chat; `snapshotDemo` takes it back when the tour ends.
The user then draws their own rectangle and chooses a colour. The
`highlight.created` event carries `kind: "text" | "area"`, so each practice
step completes only for its matching type.

- Actions: `{click: anchor}`, `{type: anchor, text, speed?}`,
  `{press: "Enter", on?: anchor}`, `{waitFor: {event, match?}, timeout?,
  status?}`, `{wait: ms}`, and `{point: anchor, wait?}`, which moves the
  spotlight and the pointer to what the demo just made and stays a moment
  (1.2 s by default) without touching it. Click and type move the spotlight
  and the drawn pointer to the element first, pause a beat, then act.
  Typing goes through the native value setter plus an `input` event, so
  React-controlled inputs see it. Meanwhile the pointer waits at the
  field's left edge, just below it (above it at the bottom of the window),
  faded out, so the typed text is the thing to watch. The note demo does
  the same over the note. Keys are dispatched as `KeyboardEvent`s (the engine's own
  hotkeys ignore untrusted events); a press `on` a field shows the key as a
  cap beside the pointer for 600 ms. Events that fire during the step are
  buffered, so a `waitFor` after a fast action still catches its result.
  A `waitFor` with a `status` shows it on the card with a spinner while it
  waits ("Fetching the paper…" for the add demo's download); after 20 s the
  card adds "This can take a minute on a slow connection."
- The demos above are actions too: `{previewHighlight: true}`,
  `{previewArea: true, context?}` and `{note: text}`, which types into the
  note block App's `prepareNote` opens, through CodeMirror, resuming an
  unfinished example and never replacing the user's own writing.
- Typed text (`type`, `note`) is `T()`-marked in the tour and translated
  when typed. `{name}` in it is filled from the tour's `vars` (an object, or
  a function of the facts: the first tour's `demoUrl`), which the
  localStorage key `gamma-guide-vars` overrides — how the browser suite
  points the demo at an uploaded PDF of its own.
- From the demo's initial pause through its final action, the sheet swallows
  clicks, the card shows ▶ Watch and a thin line of the actions done so far,
  and Back/Next are hidden. **Skip this demo** stops the actions (their
  cleanups run) and moves on, except on a step marked `skippable: false`
  (the add-a-paper demo, which every later step depends on). On a step that
  hands over to the user, Skip stops the demo and stays for the try
  (`skipDemo` in `useGuide.js`). Arrow and Enter navigation is paused too;
  the close button and Esc still leave the tour. After the actions, a step
  without `advanceOn` advances by itself, one with it hands over to the
  user: a demo and its try are one step (the first tour's highlights). A failed action (anchor never appeared, event
  timed out) leaves the card up with "couldn't finish" and Skip; the tour is
  never stuck.
- `placement` on a step names the card's preferred side so it does not cover
  what the demo is about to open (the Add step sits to the left of the
  button; its popover opens below). While a demo works inside the step's
  own anchor (the note demo in the Notes window), the card keeps clear of
  that whole anchor, so it covers nothing the demo uses next.

The survey behind these choices is [docs/research/onboarding.md](../research/onboarding.md).

## Illustrations

A step may carry a drawing above its copy: `media: "<id>"`, an id of the
registry in `guide/media.js`, whose file is `guide/media/<id>.svg`. It is the
same contract as an anchor — a tour names an illustration and never a path,
and `npm test` fails on an id with no file, a file no step shows, and a file
the registry does not know.

**What a drawing is for.** A concept that has no place on screen. A gesture
on the real UI is a scene's to show ("Scenes", above), so a drawing never
shows a control, a click or a drag. Two ideas have earned one: one paper
filed in several folders and never copied (`labels-folders`, the library
tour), and one page behind both views, the notes and the notebook view
(`page-notebook`, the Add and notebook tours).

**Why it is inlined.** `GuideOverlay` puts the file's markup straight into
the card rather than loading it as an `<img>`, so it paints from the theme's
tokens. There are eight themes, so a drawing carrying its own colours — or
deciding them from `prefers-color-scheme` — would be wrong in most of them.
The files are this repository's own, built into the bundle by
`import.meta.glob(… "?raw")`; nothing a user supplies is ever inlined.

**One grammar** (`guide/media.css`). A drawing is plain shapes carrying the
stylesheet's classes, with no `<style>`, colour or words of its own, so the
drawings look like one set:

- **Shapes**, painted from tokens: `m-card` (a surface: a paper, a panel, a
  sheet), `m-well` (a sunken area), `m-line` and `m-line-dim` (lines of
  text), `m-rule` (a ruled line), `m-chip` and `m-chip-on` (a pill), `m-glyph`
  and `m-glyph-on` (an icon's stroke), `m-link` (the accent connector or
  outline) and `m-ink` (handwriting). The accent goes on the one thing the
  drawing is about.
- **Motion**, on one clock: `--m-t` (an `@property` number) runs from 0 to
  1 over the period `--m-period` (4.8 s), twice, then rests at 1. An
  element's motion class starts at its `style="--m-at: …"` (0 to 0.7 of
  the period): `m-appear` fades in and settles, `m-draw` draws a stroke
  along its path (`pathLength="1"`), `m-light` lights an accent mark. A
  drawing's order is data, and its rhythm is every drawing's.
- **The still picture is the whole one.** At `--m-t: 1` every element is
  complete, and that is where the drawing rests, what reduced motion shows
  and what a browser without `@property` shows.

**What a drawing must do**, each checked by `tests/guide.test.mjs`: open
with a comment saying what it shows; use only plain shape elements, the
geometry attributes and `media.css` classes (every class it uses has a
`.guideMedia .<class>` rule); no `<style>`, `<text>`, raw colour, id or
event attribute; no inline style but `--m-at`; `pathLength="1"` on every
`m-draw`; no `transform` on an `m-appear`; a `viewBox` and no
`width`/`height` on the root. The registry's `ratio` (1.4–2.2 — the card is
320 px wide) gives the box an `aspect-ratio`, so the card is its final
height when `place.js` measures it and a picture never moves its own card.
`media.css` itself is scoped under `.guideMedia` and paints from tokens.

A drawing goes on a step of a running tour only. An offer and a hint stay a
card of words: they interrupt, so they stay small.

## Goals

1. A new account reaches the "aha" (a highlight that became a note block under
   an open paper) in under two minutes without reading docs.
2. The guide points at the real UI, not at screenshots. What it teaches is
   done in place, and the guide notices when it was done.
3. Moving, renaming or removing a control never silently breaks the guide.
   The failure mode is a named test failure, never a card pointing at nothing.
4. Adding or changing a step is editing data in one file, not React code.
5. Nothing is forced. Every surface is dismissable, every tour can be
   replayed from one place, and the share view never shows any of it.

## Three layers

Onboarding is three independent surfaces over one shared engine. Each can be
edited or switched off without the others.

| Layer | What it is | Driven by | Where the content lives |
|---|---|---|---|
| **Welcome page** | A real page in the new workspace ("Welcome to Gamma"), with its own sample PDF attached, so every tour step has something to act on | Seeding | `backend/gamma/onboarding/welcome.md` (imported through the `.md` parser at seed time) |
| **Tours** | Sequential coach marks: a spotlight on one control plus a card. Task-driven: a step advances when the user does the thing, or on Next | The guide engine | `frontend/src/guide/tours/*.js`, one declarative script per tour |
| **Hints** | One card right after the first use of a feature, beside the control, no dimming | Triggers (state or event), not sequence | `frontend/src/guide/tours/hints.js`, one-step triggered guides |

The order matters for reconfigurability: content (the welcome page) changes
most often and costs nothing to change; tours change when the UI changes and
are protected by tests; the engine changes rarely.

## Anchors: the contract between UI and guide

The one rule that makes the guide survive UI changes: **the guide never
selects by class name, text or DOM position.** It selects by anchor id.

- An anchor is a `data-guide="<id>"` attribute on the element the guide should
  point at. Ids are dotted and named by meaning, not by look:
  `header.add`, `header.share`, `add.urlInput`, `pdf.textLayer`,
  `dock.notes`, `notes.table`, `account.tour`, `chat.composer`, `share.link`.
- `frontend/src/guide/anchors.js` is the registry: every id with a one-line
  description, the view it lives in (`home` / `page` / `pdf` / `any`; any
  other view — `chat`, `table`, `presence`, `merge`… — names the situation
  that brings the anchor up), for anchors inside a menu or popover the
  `open` path the engine clicks first, and `pick` when the anchor repeats:
  `"last"` for the newest (the latest chat reply's citation), `"recent"` for
  the one inside the element the app marks `data-guide-recent` (the table
  the user just made, below), else the first.
- Adding an attribute in JSX without registering it, registering an id no
  element carries, or referencing an unregistered id from a tour fails
  `npm test`; the scan also reads a conditional
  `data-guide={cond ? "id" : undefined}`.
- Anchors move with the JSX they decorate. When a control is deleted, delete
  its registry row; the tests then name every step that referenced it. The
  registry may keep a control no tour points at yet (`header.search`,
  `chat.context`…), as long as an element carries it.

Because App.jsx is still being decomposed ([frontend-refactor.md](frontend-refactor.md)),
anchors are the only thing the guide needs from it. No guide code imports App
state directly; App passes what the engine needs through one `useGuide()`
call: the facts steps and triggers are matched against (`view`, `hasPdf`,
`pdfChatVisible`, `aiConfigured`, `aiEditable`, `onPage`, `editable`,
`phone`, `guideAvailable`, `sharedWorkspace`, `shareAudience`,
`unfiledLibrary`, `emptyLibrary`, `growingLibrary`, `annotatedPage`,
`notebookView`, `viewerTools`, `clonedWorkspace`, `installable`, `connectorHere`,
`cloudLinkable`, `prefsChanged`, `demo`, `welcomePdf`), the
services demos, scenes and tour ends call (`show`, `restore`, `openSettings`,
`findEquation`, `findFigure`, `snapshotDemo`, `prepareNote`, `dockZone`)
and `tidy`.

## Tour scripts

A tour is data, one file in `frontend/src/guide/tours/`, registered in
`tours/index.js` (whose order is the Tours menu's). Every string is marked
`T()` and translated when shown or typed ([i18n.md](i18n.md)).
`tours/tables.js`:

```js
export default {
  id: "tables",
  version: 2,
  title: T("Editing tables"),
  requires: { onPage: true, editable: true },
  trigger: { event: "table.created" },
  offerAnchor: "notes.table",
  offer: { title: T("You made a table"), line: T("Cells, rows and columns are edited in place.") },
  steps: [
    { id: "table-make", anchor: "dock.notes", placement: "left", creates: "notes.table",
      title: T("Type /table in a note"), body: T("Then click outside it."), advanceOn: { event: "table.shown" } },
    { id: "table-cell", anchor: "notes.table", placement: "bottom",
      title: T("Click a cell and type"), body: T("{key:Tab} moves on, {key:Enter} saves."),
      scene: [{ click: "notes.table", at: [0.25, 0.75] }], advanceOn: { event: "table.edited" } },
    { id: "table-add", anchor: "notes.tableAdd", avoid: "notes.table", placement: "bottom",
      title: T("+ adds a row or column"), body: T("Hover one for its handle: drag it, or click for options."),
      scene: [{ click: "notes.tableAdd" }] },
    { id: "table-whole", anchor: "notes.tableCorner", avoid: "notes.table", placement: "top",
      title: T("The corner selects the whole table"), body: T("Then copy, move or delete it."),
      scene: [{ click: "notes.tableCorner" }], next: T("Done") },
  ],
};
```

Tour fields:

- `id`, `version` (an integer; bump it only when a finished user should be
  offered the tour again), `title` (the Tours menu and the offer).
- `requires`: facts that must hold to start or offer it. A value that is an
  array means one of its values.
- `trigger`, `offer: {title, line}`, `offerAnchor`, `offerPlacement`: when
  and how it is offered by itself (Triggered tours and hints, above).
- `hint: true`: a one-step triggered guide, never in the Tours menu.
- `show`: a surface App brings up before the first step (`"chat"`).
- `restore`: what App restores when the last step is done (`"pen"`).
- `welcome: true` with `minutes`: the first `intro` step is the centred
  welcome card, which is also the offer.
- `finishCard`: the card the tour ends on (`guide/finish.js`).
- `vars`: values for `{name}` in typed text, an object or a function of the
  facts.
- `sibling`: the tour that teaches some of the same controls (Sibling
  tours, above).

Step fields:

- `id`, `anchor` (null: a centred card; an anchorless hint sits in the
  bottom-left corner), `title` (about six words) and `body` (at most one
  short sentence, only when the title cannot carry it; `bodyTouch` words it
  for a touch screen). Both take `**bold**`, `*italic*`, `` `code` `` and
  `{key:…}`.
- `scene`: a looping demonstration on the real UI that changes nothing
  ("Scenes", above).
- `media`: an illustration id from `guide/media.js`, drawn above the copy,
  for a concept with no place on screen (Illustrations, below).
- `placement`: the card's preferred side (`top`, `bottom`, `left`, `right`),
  or `inside` to tuck it into the anchor's bottom-right corner (the whole
  PDF viewer). `avoid`: another anchor whose box the card keeps clear of as
  well (the table above its add strip).
- `requires`: facts for this step; a step whose facts do not hold is left
  out of the run when the tour starts (the AI chat tour's steps per place).
  Nothing is ever shown for a step that does not apply.
- `advanceOn: {event, match?}`: the user's action that ticks the step (✓
  Done, then on). The step keeps Next unless it `creates` something,
  when it waits for the action ("Your turn").
- `next`: the primary button's label (Done, Finish).
- `do: [...]`: a demo (Demo steps, above), with `delay` (the pause before
  the first action, 900 ms) and `skippable: false`.
- `optional`, `creates`, `reveal`: see the engine abilities above.
- `shared`: a step the sibling tour also teaches, left out once the
  sibling is done (Sibling tours).
- `intro`, with `outline`, `later` and `footnote`: the welcome card.

`open` paths live in the registry, not on steps.

## The event catalog

The app emits a small number of named events; tours advance on them and
triggered tours are offered from them. `frontend/src/guide/events.js` exports
`guideEvents.emit(name, payload)` and the catalog, each emitted at one point
where the thing happens:

| Event | Emitted by |
|---|---|
| `popover.opened` `{name}` | App, when a topbar popover opens |
| `page.opened` `{id, title}`, `home.opened` | App's page open and `goHome` |
| `nav.pushed`, `nav.back` | App's `pushNav` (a link jump recorded where you were) and `goBackNav` (the Back button or Alt+←) |
| `palette.opened` | App, when the Ctrl+P palette opens |
| `highlight.created` `{id, kind}` | App's highlight creation path |
| `chat.sent`, `chat.cited` | ChatDock's send, and the end of a reply holding a citation (`gammaLinksIn`) |
| `citation.shown` | PdfCitationOverlay, once a quote is found and marked |
| `share.created` | App's `createShareLink` (the Share popover's first audience tile, or its first invitation) |
| `peer.joined` | App, when another account or a link visitor appears on the open page (a peer whose `user` is not this account) |
| `ink.stroke` | App's `handleInkStroke` |
| `ink.options`, `ink.erased`, `ink.undone` | App, when the armed tool's options row opens; `handleInkErase` / `handleInkErasePartial`; `inkUndo` (not redo) |
| `table.shown` | MdTableWrap, when an editable table mounts |
| `sheet.created` `{id}` | App's three ways to make a sheet of paper: `addSheetAfter` (**+**, Add page, writing low on the last sheet), `insertSheetAt` (`/note` in an empty block) and `createNotebook` |
| `job.started` `{kind}` | the tasks store's `adopt` — the one place both `start()` and `upload()` hand this tab a server job, so every kind counts once ([tasks.md](tasks.md)) |
| `approval.shown` `{tool}` | `chat/ApprovalCard`, while it is up ([ai.md](ai.md) "Asking before a call") |
| `paper.fetched` | App's `openPdf`, once a paper fetched from an address (a URL, a DOI, an arXiv id) is open — not for a page the library already had |
| `table.created`, `table.edited` | MdTableWrap: the first render of a table made with /table or a paste, and a cell editor's commit. BlockTree notes the block and where in it the table went (`noteTableMade`); the table that renders there takes `data-guide-recent`, so the table anchors (`pick: "recent"`) point at it and not at an older table on the page |
| `conflict.shown` | MergeResolver's versions |
| `ref.search` | BlockTree, when the `[[` search shows results |
| `math.previewed` | MathLivePreview, when it comes up |
| `settings.opened` `{pane}` | App, whenever Settings opens (on any pane) |
| `block.created`, `block.indented` | catalogued, not emitted |

A test asserts every `advanceOn`, trigger and `doneOn` name is in the catalog;
emitting a name outside it logs a warning and does nothing.

Events are the seam that keeps tours out of App.jsx: instrumenting a new
event is one line at the point where the thing happens, and every future tour
can use it.

## The engine

`frontend/src/guide/useGuide.js` holds the state; `GuideOverlay.jsx`,
mounted once in App, renders what it says.

- **State**: at most one of a run (`{tour, steps, index, done, finishing}`:
  the steps left after `requires` and `creates`, the current one, the Done
  moment, the finish card) or an offer (`{tour}`). A synchronous
  `activity` ref reserves the one surface, so an offer never lands on a
  starting tour. Progress is written as it goes (Storage, below).
- **Finding the anchor**: `anchorElement(id)` in the registry, re-measured on
  every DOM mutation, resize and scroll plus a 250 ms retry while it mounts.
  An anchor that has not appeared after 3 s skips the step (the overlay logs
  `guide: anchor "…" not found`, except for an `optional` step, which passes
  silently) or withdraws the offer. A demo action waits up to 4 s for its
  anchor, then shows "couldn't finish".
- **Spotlight**: one fixed SVG with an even-odd path: the dimmed sheet with a
  rounded hole around the anchor (or around its `surface`), padded 6 px,
  and a pulsing accent ring on the anchor. The hole is unpainted, so
  pointer events reach the real control; the painted sheet swallows
  clicks. While a demo acts, a shield swallows clicks in the hole too;
  while the user presses inside it, the sheet lifts. The anchor is scrolled
  into view (`nearest`) when the step opens. Changes inside the guide's own
  root (a scene's shapes, the demo pointer) never re-measure it.
- **Card** (`place.js`): 320 px, the popover look (same tokens as the account
  menu, [ui-design.md](ui-design.md)). It goes on the step's `placement`
  when that fits, else below, above, right, left, each judged clear of the
  spotlight and of its `avoid` box (the step's `avoid` anchor, the anchor's
  surface, or the demo's step anchor); clamped to the viewport; a beak
  on the edge that faces the anchor. At 640 px and narrower it is a bottom
  sheet (above the phone layout's tab bar). It is `role="dialog"` with
  `aria-live="polite"` and never takes focus: mousedown is prevented, so an
  editor keeps its caret.
- **Keys**: Esc leaves the tour (or dismisses an offer); → and Enter
  advance, ← goes back, never inside a text field; a demo swallows them
  while it acts. The demo's own synthetic keys are ignored.
- **No dependency.** The positioner is `place.js`, about sixty lines.

## Starting and replaying

Open **Account > Tours** and choose a tour; the menu lists the ones that can
start here (see Manual tours), and every tour can be replayed. Triggered tours
and hints also come by themselves, once each (see above), unless Suggest
tours is off. AI chat opens its dock if needed; PDF-specific steps are
included only when the PDF is visible. The share view shows none of it
(`useGuide`'s `enabled` is false there), and no URL starts a tour.

## Storage

Progress is per browser. Each tour has one localStorage key per account,
`gamma-guide:<account>:<tourId>` (the first tour keeps its older
`gamma-guide:first-run`), holding `{state, step?, version}`: `offered`,
`running` (with the step), `dismissed` (with the step it was left on) or
`done`. A tour whose stored `version` is its current one is not offered
again. On a demo server the same keys live in sessionStorage
(`guideStorage(demo)`), and when storage is unavailable a memory copy keeps
the offers for the page load. `gamma-guide-vars` overrides a tour's `vars`
(the browser suite points the first tour's demo at its own PDF).

Whether tours are suggested at all is the account's `suggestTours`
preference, synced with the profile ([settings.md](settings.md)).

## The welcome page and its sample PDF

- Content is `backend/gamma/onboarding/welcome.md`, a normal markdown outline
  (front matter `title`, the page's, and `document`, the PDF's), parsed at
  seed time with the importer's `md_to_blocks`, so what the importer supports
  the welcome page supports (callouts, math, images, tables). Editing copy is
  editing markdown. It is English only; there is no per-language welcome.md.
- The page carries a PDF so "select text to highlight" has a target. The PDF is
  **rendered from the same markdown by the notes-as-PDF writer**
  (`pdf_document.render_document`, [import_export.md](import_export.md)).
  The writer is deterministic, so it runs once per process
  (`seed.welcome_pdf`). No binary asset in the repo, no licence question,
  always in step with the text, and it demonstrates the export feature.
- The PDF goes through the content-hash store like any upload, with
  `store_file`, not `store_pdf`: the latter's background manifest walk could
  still hold `data.db` open when a guest who just arrived logs out.
  `/api/pdf-info` makes the manifest on first open. A PDF the storage limits
  refuse leaves the page without one.
- Its last section is a practice passage. Its plain-text formula line is
  what the first tour's box demo encircles (`findEquation` looks for
  `Attention(` and `softmax`). The page's own `meta` record (`source:
  manual`) and `ppt_cite` mean opening it looks nothing up and asks no AI
  for a citation.
- `seed.seed_welcome(ws, actor=, guest=)` seeds it into a workspace with no
  pages yet. Every account-creating path asks for it through
  `workspaces.ensure_personal(..., welcome=True)`: `seed.create_account`
  (the admin API, `manage.py create-user`), `seed.create_cloud_account`,
  `seed.ensure_admin_seed` and `guests.new_guest`. `workspaces.create`
  seeds once the workspace's rows exist.
- An existing workspace, a further personal workspace, a shared one and an
  offline copy start empty. `manage.py setup` seeds nothing, except a guest
  workspace whose files it recreates. A share host seeds nothing either: its
  workspaces hold published pages only, each counted against the plan's cap
  ([cloud_accounts.md](cloud_accounts.md)).
- A guest's page ends with a callout naming the lifetime; a
  `GAMMA_GUEST_SEED` zip replaces the whole workspace ([guests.md](guests.md)).
- The page has `properties.seeded: "welcome"`, so the first tour finds its
  PDF and the library can tell a fresh one (no pages but seeded ones) from a
  used one. Deleting it is fine; the tour then falls back to arXiv.
- The notes go in as one op batch (`apply_ops` + `after_commit`, what
  `commit_ops` does, on a connection closed right there) after
  `blocks_store.create_page` made the page ([collab.md](collab.md)).

## Hints and empty states

Hints are one-step triggered guides in `tours/hints.js` (see Triggered
tours and hints). The chat with no AI provider gets no hint: its empty state
is the setup card with a tile per service. Empty states in the library and
the notes column stay where they are; hints point at controls, empty states
explain areas. A library with no pages of the user's own shows "Start your
library" (`home.empty`, [home_library.md](home_library.md)), whose tour link
starts the first tour. App works out that fresh-library state once
(`isFreshLibrary` over the loaded listing) for both that card and the
`emptyLibrary` fact.

## Files

```
frontend/src/guide/
  anchors.js        registry: id → {description, view, open?, pick?}; anchorElement
  events.js         guideEvents bus + the catalog
  triggers.js       when a tour is offered; per-account progress
  keys.js           {key:…} in copy: a command's chord for this account
  media.js          illustration registry: id → {ratio, description}
  media/*.svg       one drawing per id, drawn with media.css's shapes
  media.css         the drawings' shared grammar: shapes, motion, the card's media box
  finish.js         a tour's finish card: what the run made
  place.js          where a card goes beside its spotlight
  useGuide.js       state machine, offers, reveal, demo actions
  scene.js          a step's scene: its primitives, MOTION (the guide's timing) and the player
  previewHighlight.js, previewArea.js, typeDemoNote.js   the demos that drag or type
  GuideOverlay.jsx  spotlight + scene layer + card + bottom sheet, offer, hint and finish cards
  tours/*.js        one file per tour; tours/index.js registers them
  tours/hints.js    the hints
  guide.css
backend/gamma/onboarding/welcome.md   the seeded page (and the sample PDF's source)
backend/gamma/seed.py                 seed_welcome: parses it, renders the PDF, one op batch
backend/tests/test_welcome.py         who gets it, the PDF, the op batch, the fallbacks
frontend/tests/guide.test.mjs         schema, anchor references, event names, triggers, placement
frontend/tests/e2e/scenarios/guide.mjs            the first-run tour end to end, home anchors present
frontend/tests/e2e/scenarios/contextualGuide.mjs  Arrange windows (its scenes, then the user's own fold
                                                  and drag); the AI chat tour on the library, a paper,
                                                  a hidden PDF, a phone, and with no AI connected
frontend/tests/e2e/scenarios/auth.mjs             demo mode: the guest lands and gets the first-run offer (sessionStorage)
frontend/tests/e2e/scenarios/triggeredGuide.mjs   offers and hints: tables (made by /table or a paste,
                                                  not merely shown, pointed at the new one), sharing
                                                  (offered, and from the menu with and without a link),
                                                  citations, math, Ctrl+P, Back, workspaces, presence,
                                                  handwriting from the menu (draw, note, style, erase,
                                                  lasso, the pen re-armed), Suggest tours off
```

## Validation

- `npm test` (`guide.test.mjs`): every step's `anchor`, `creates`, `reveal`
  and `avoid` and every offer anchor is in the registry; every step's
  `media` is a registered id with a file, every registered drawing is shown
  by a step, and each one is built only from `media.css` classes, takes
  its colours from tokens, sizes itself from a `viewBox` and declares a
  ratio between 1.4 and 2.2; every registered
  anchor is carried by an element and every `data-guide` in the source is
  registered; every `advanceOn`, trigger, `doneOn` and finish-card event is
  in the catalog; ids are unique; `version` is an integer; a hint is one
  triggered step; the trigger rules (event, `count`, `doneOn`, state, the
  trigger's `requires`, `anyOf`, version) behave; progress survives a
  reload, separates accounts, tolerates broken storage and stays in
  sessionStorage on a demo server; the AI chat and sharing tours pick their
  steps per situation; the viewer tours share their steps and drop them
  once the sibling is done; every `{key:…}` names a command or a key; offer copy
  is catalogued; the finish card lists only what the run made; a repeated
  anchor picks as its `pick` says; a card keeps clear of its `avoid` box;
  every scene primitive is a known kind naming registered anchors (a dock
  zone names a side and a window) and no hint has a scene or a drawing; a
  surface is a registered anchor opened by the same path as the anchors in
  it.
- `guide.mjs`: every `home` anchor without an `open` path is present once;
  the first tour runs end to end from the Tours menu (welcome card, the add
  demo, both highlights with their demos and the light hand-over, the note
  and label demo, Home, the finish card's items and tiles), Skip on a
  highlight demo stays for the try, and the tour replays on the paper
  already open; without the
  vars override its demo opens the Welcome page's PDF with no resolver call,
  and falls back to arXiv once that page is deleted; a new account's empty
  library is offered the tour once.
- `contextualGuide.mjs`: Arrange windows — its double-click scene lands on
  the real title and changes nothing, the steps keep Next, the drag scene's
  drop preview is the left dock zone clear of the card, the sheet lifts and
  the scene steps aside while the user drags, and their own fold and drop
  tick the steps; the AI chat tour's steps per place, the figure snapshot
  taken back when the tour ends unsent, and the setup card with no AI
  connected.
- `triggeredGuide.mjs`: each offer appears after its event without dimming
  the app; Show me runs the tour (inside the Share popover and the account
  menu without closing them); the Add popover is the spotlight's surface,
  its other rows clickable, and its typing scene types nothing for real;
  the library's scene right-clicks a card; a hover-only control shows while pointed at;
  the tables tour points at the table just made and its cards keep clear of
  it; the PDF viewer tour is listed and walked on a PDF without a table of
  contents, and after it the notebook view's offer is two cards that end
  as done when the user clicks Notes view; a hint keeps the editor's caret; a `creates` step or an optional step
  whose anchor is there or gone passes without a warning; nothing is offered
  twice, or at all once Suggest tours is off in Settings.
- Backend (`test_welcome.py`, `test_guests.py`): a new account's workspace
  holds the welcome page with its PDF, `seeded` property and one op batch; a
  guest's names the lifetime; existing, shared and non-empty workspaces are
  never seeded; a refused PDF or a missing welcome.md never fails the
  account.

## Rules

- New control worth teaching: add `data-guide`, register it, done. Never point
  a step at a class.
- New feature worth a triggered tour: emit an event where it happens (one
  line, catalogued), write `tours/<name>.js` with a `trigger`, register it in
  `tours/index.js`, add its strings to the catalog. A single card is a hint.
- A gesture worth showing (a double-click, a drag, where something lands):
  a `scene` on the step, from the primitives above, on registered anchors.
  Add a primitive to `guide/scene.js` only when no combination shows it.
- A concept with no place on screen (one page behind two views, one paper
  in many folders): a drawing — `guide/media/<id>.svg` and a row in
  `guide/media.js` — named from the step's `media`. Keep pointing at the
  real control (Illustrations).
- Copy: a title of about six words, a body only when needed, never a
  sentence that narrates what the scene or the demo shows.
- Renaming or removing a control: update the registry row; the tests name the
  affected steps.
- Copy changes: edit the tour file or `welcome.md`. Bump `version` only when a
  finished user should see the tour again.
- Keep this doc and [settings.md](settings.md) (the `suggestTours`
  preference) in step.

## Not built

Two parts of the first design were never built, and the September 2026 UI
review turned them down for now:

- **A synced `onboarding` preference.** Progress stays per browser (see
  Storage), so a tour finished on one computer is offered again on another.
- **A Getting-started checklist**: a few first tasks (add a paper,
  highlight, write a note, search, share) ticked from the event bus whether
  or not a tour ran, each with a way into the matching tour.
  `block.created` and `block.indented` are catalogued for it and emitted
  nowhere.
