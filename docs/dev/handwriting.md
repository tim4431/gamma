# Handwriting (ink) annotations

Draw on a PDF page with a stylus, mouse or finger; the strokes become a
block in the page's notes. The survey behind the shape, and how Notability
does the same things, is in
[research/handwriting.md](../research/handwriting.md); what Notability and
GoodNotes store, and why the file looks the way it does, in
[research/ink-storage.md](../research/ink-storage.md). Code:
`gamma/ink.py` + `gamma/routers/ink.py` (server), `frontend/src/ink/ink.js`,
`ink/finishStroke.js`, `ink/shapes.js`, `ink/gestures.js`,
`ink/stabilizer.js`, `ink/fingerChords.js`, `ink/stripKeys.js`,
`ink/pencilActions.js`, `ink/inkStore.js`, `ink/InkLayer.jsx`,
`ink/inkInput.js` (client), with the
tool strip and the page plumbing it shares with text boxes in
`markup/MarkupToolbar.jsx`, `markup/MarkupLayers.jsx` and
`markup/PageTools.jsx`; tests `backend/tests/test_ink.py`,
`frontend/tests/ink.test.mjs`, `finishStroke.test.mjs`, `shapes.test.mjs`,
`gestures.test.mjs`, `stabilizer.test.mjs`, `fingerChords.test.mjs`,
`stripKeys.test.mjs`, `pencilActions.test.mjs`, `inkInput.test.mjs`, the
shared fixtures `tests/shared/inkv2.json`, `inkwidth.json` and
`inkmerge.json` (read by `backend/tests/test_shared_fixtures.py` and the
node tests) and `inkshapes.json`, `inkscribble.json` (the recognisers run in
the node tests only, the server never recognises; the Python side checks
the fixtures' shape and that every stored `shape` record parses), e2e `tests/e2e/scenarios/ink.mjs`
and `inkEditing.mjs`. The same files are
written on notebook sheets ([notebooks.md](notebooks.md)) and by the iPad
app ([ipad.md](ipad.md)). Typed text on a page is
[text_boxes.md](text_boxes.md).

## What the user sees

- The pen button in the viewer's zoom column opens the **tool strip** at
  the top of the page, laid out like Notability's. Its first row is five
  groups, each split from the next by a thin rule (`.pdfInkSep`): the
  **tool presets** — each a pen or a highlighter with its own colour and
  width, shown as the icon over a colour bar (four pens and three
  highlighters to start) — then the eraser, the lasso and the Text tool
  (typed text boxes, [text_boxes.md](text_boxes.md)); a hand (nothing
  armed: scroll and select text); **More** (⋯, "More handwriting tools")
  and close; and Undo / Redo pinned at the right end, always the last two
  buttons. Its accessible name is "Markup tools". One tap arms a tool;
  **tapping the armed tool again opens its options row** under the strip.
  Opening the strip arms the last pen used.
- **More** opens a menu on the press (a `ContextMenu` of `MenuItem`s
  rendered outside the strip's tooltip host, so it takes the tooltip's
  place in the same frame): *Paste handwriting* (Ctrl+V; greyed, titled
  "Copy or cut handwriting first", until the handwriting clipboard holds
  strokes), *Select all handwriting on this page* (Ctrl+A), a divider,
  and *Handwriting settings…* (Settings → Reading & editing, at the
  Handwriting section). The first two rows are the catalog commands
  `ink.paste` and `ink.selectAll` drawn as `CommandMenuItem`s, so a
  rebound chord shows there too. Escape or a press outside closes it.
  The button is `data-guide="ink.more"` for the guide.
- **A preset's options row** is three captioned sub-rows (the caption a
  14 px line in the small caption size, hidden under 480 px of viewport
  width). **Style**: the pills *Pen* (width follows the stylus pressure)
  and *Ballpoint* (an even line at every pressure; the stored brush is
  still `monoline`, a word that never shows — the preset reads "Red
  ballpoint pen · 4 pt · key 1"), then the *Smoothing* ladder of five
  dots (Off, 1–4: the preset's `stabilize`, "Smoothing" below); on a
  highlighter the *Straight* pills (Hold · Always · Off, its `straight`,
  "Straight highlighter" below) stand where Pen · Ballpoint would.
  **Color**: the palette — the colour groups the account chose for pen
  presets or for highlighter presets (Settings → Appearance › Colors,
  [settings.md](settings.md); by default the 14 dark "Pen" colours and
  the eight light "Ink highlight" colours, the four text highlight
  colours and four more) plus a custom colour through the browser's
  picker. **Width**: eight widths as dots (titled "Width 4 pt · [ ]": the
  bracket keys step them), then *Duplicate* (a copy right after it,
  armed and still open for editing), *Remove* and a ⋯ button, the **Tool
  menu**, which opens on the press like More: *Move left*, *Move right*
  (greyed at the row's ends; the pill says "Blue pen is now key 3."),
  *Duplicate*, *Hide*, *Hidden tools ▸* (a submenu naming each hidden
  preset, "Blue pen · 2 pt", a tap brings it back; greyed, "No hidden
  tools", with none), *Remove*, a divider, and *Reset this tool* (colour,
  width and style back to the default pen or highlighter of its kind).
  *Remove* and *Hide* (the button and the menu rows) are greyed at the
  last visible preset, whether or not hidden ones remain: the row never
  empties.
  Every change applies to that preset, so the row is the user's own set
  of pens (up to 12, the account's `inkTools`, "Presets follow the
  account" below, each with the concrete hex it was given and, when a
  swatch brought one, its own `opacity` — a swatch's alpha; a preset
  without one draws at the kind's usual opacity, 0.6 for a highlighter
  and 1 for a pen, `toolStyle`). Colours are named in words, never hex
  codes: a preset reads "Pink highlighter · 14 pt · key 7", and each
  swatch is titled and labelled by its name. Any colour, a custom one or
  a changed swatch included, takes the name of the nearest reference
  colour (`colorName` in `shared/model/palette.js`, redmean distance).
  On a phone (480 px and under) Style and Width share one wrapping line
  and Color takes the next, so the open strip never stands more than
  about 110 px over the page.
- **Reorder by dragging.** A preset button follows a mouse drag of 6 px,
  or a finger or pen held 500 ms and then moved, along the row: a ghost
  of the button rides with the pointer, a 2 px accent bar shows where it
  will land, and the pill says "Drop to reorder." The drop writes the new
  order (ids unchanged) and the number keys follow it; dropping outside
  the strip, or Escape, leaves the order as it was, and a hold with no
  movement is a plain tap that arms the tool. **Hide** takes a preset off
  the row, out of the `1`–`9` / `P` / `H` cycles and away from the stylus
  (a hidden pen is no longer the pen a stylus writes with; the next
  visible pen is) (the pill: "Blue pen hidden." with *Show*); *Hidden tools* in any preset's ⋯ menu brings it
  back where it was. Keys `1`–`9` always count the visible presets in
  row order.
- **Presets follow the account.** `inkTools` lives in the profile
  ([settings.md](settings.md) "Reading & editing"): a browser that signs
  in takes the account's list, and an account without one is seeded from
  the browser. When the server's list differs from a list this browser
  had changed itself (not the defaults), one pill asks "Use this
  browser's pens instead?" with *Use this browser's*, which pushes the
  local list; the pill appears on that first pull only (a later pull
  that differs is another device's edit of the account's list, which
  simply applies), and otherwise the account's pens stand. Every write
  of the list goes through `normalizeTools` (App's `setInkTools`), so
  what this browser keeps and pushes is exactly what a pull reads back. Which pen a stylus writes with stays session
  state; the eraser's mode and size and the lasso mode stay this
  browser's.
- Keys while the strip is open (`ink/stripKeys.js`, matched by the
  physical key, so a layout or Shift does not matter): `1`–`9` arm the
  visible preset at that position, `P` / `H` step forward through the
  pens / highlighters and `Shift+P` / `Shift+H` backward, `[` / `]` make
  the armed preset thinner / thicker through its eight widths (the pill:
  "Blue pen · 2.8 pt.", options row open or not; at the end of the ladder
  the pill repeats the width), `L` `T` `V` the lasso / Text tool / hand,
  `Delete` (the lasso selection), and **`Ctrl+Z` / `Ctrl+Shift+Z` step
  the strokes** (each stroke or selection edit is one entry; the history
  is per visit of the page). **`E` taps or holds**: a tap arms the eraser
  and it stays armed (a second tap opens its row, as a tap on the button
  does); held 300 ms or more while an erasing pass is made, letting go
  returns to the tool armed before it, and a lasso selection made before
  the hold comes back with the lasso. `Esc` closes one layer at a time: a
  selected text box, then the ink selection, then an open options row,
  then the strip. Ctrl+C / X / V / D / A are not strip keys but catalog
  commands (`ink.copy`, `ink.cut`, `ink.paste`, `ink.duplicate`,
  `ink.selectAll`, "Copy, cut and paste" below), so Settings → Keyboard
  lists and rebinds them. While the Text tool is armed or a text box is
  selected or edited, `Ctrl+Z` is left to the block history, and `Esc`
  to a selected or edited box.
- The **eraser**'s options row: *whole strokes* removes anything it
  touches, *partial* cuts through them (the pieces on either side become
  their own strokes), three sizes, then *Erases* (All · Pens ·
  Highlighters, the account's `inkEraserFilter`: what any erasure — the
  eraser tool, the pen's eraser end and barrel button, a scribble — may
  remove; a stroke of an unknown tool goes only under All; with a filter
  on, the hover ring carries a small pen or highlighter glyph) and a
  *Back to last tool* toggle (`inkEraserReturn`, off by default: lifting
  the **tool** eraser returns to the tool armed before it with the options
  row closed, whether or not the pass erased anything; the pen's eraser
  end and barrel never switch tools, since lifting them is the switch, and
  a gesture ended by `pointercancel` does not return). The
  **lasso**'s row: *freeform* circles strokes (more than half their
  samples inside), *box* drags a rectangle; the dashed box then moves by
  dragging and deletes with `Delete`. Both work across groups on the page.
- **Draw and hold** (`inkDrawHold`, the account's, on by default): draw a
  line or a shape and keep the pen still at its end; after 600 ms the
  stroke snaps to the recognised shape — a line (snapped to 0°, 45° or 90°
  when within 5°), an arrow (a line with a short symmetric head), a
  triangle, a rectangle (axis-snapped within 5°; a square when the sides
  are within 10 %), a regular polygon of 5 to 8 sides (only convex and
  closed: a zigzag never becomes a hexagon), a circle or an ellipse — and
  flashes in the selection blue for two frames. The hold counts only when
  the stroke's chord is at least max(24 css px, 18 pt) (so zoom cannot turn
  a letter into a line) and the stroke lasted at least 250 ms before the
  tip went still; the timer restarts whenever the tip moves more than 3 css
  px, so a pause mid-letter needs a real stop. Keep holding and move 6 css
  px or more to refit: a line follows the tip as its new endpoint, a closed
  shape scales about its centre and rotates with the tip (Shift snaps the
  rotation to 15°; a rectangle or triangle keeps its proportions). Moving
  more than 12 css px within 150 ms of the snap cancels it and the
  freehand stroke goes on; lifting before the timer fires commits the
  freehand stroke. With a mouse, Shift while drawing straightens at once;
  it is part of Draw and hold, so off with it. A highlighter snaps to
  lines only. The committed stroke is an even line
  (`pen: false`, no pressure) in the preset's colour and width, its `t`
  spread over the time the stroke took, carrying a `shape` record ("The
  stroke file"); the pill says "Straightened." or "Shape: circle." with
  Undo one tap away, every time. On the iPad app the shell owns the pen
  and sends samples at the lift only, so nothing is recognised there yet
  ([ipad.md](ipad.md)): a shape is never recognised at commit time
  without the live preview.
- **Smoothing** (a preset's `stabilize`, 0–4, Off by default): the pen
  tip pulls the line behind it on a string of 3, 6, 10 or 16 css px (the
  lazy brush of Krita and Xournal++), so a shaky hand draws a calm line;
  pressure and time are untouched, a sharp reversal (over 90°) still
  makes a corner, and the lift catches the line up to the tip. It applies
  while drawing only: saved ink is never re-smoothed, and the live line
  is the committed line.
- **Straight highlighter** (a highlighter preset's `straight`, Hold by
  default): *Hold* straightens through draw-and-hold; *Always* replaces
  every highlight by its chord on lift when the whole stroke stays within
  min(0.75 × width, 6 pt) of that chord and the chord is at least four
  widths long, snapped horizontal within 4°, and keeps a free one free;
  *Off* never straightens. The first *Always* straightening of a session
  says so once on the pill. No snap to the PDF's text lines.
- **Scribble to erase** (`inkScribbleErase`, the account's, off by
  default; turning it on shows a pill explaining the gesture): a fast
  dense back-and-forth with a pen — at least four sharp reversals along
  its own long axis, within two seconds — erases the pen strokes under it
  (those at least 70 % inside its box, never a highlighter, never a stroke
  more than three times its size, and only what *Erases* allows) as one
  undo entry, "Erased 3 strokes." on the pill with Undo; a scribble over
  nothing is ink like any other. Highlighter strokes and shapes a hold
  produced are never scribbles.
- **Tap existing ink to select it.** With *Draws with: Pen only* (the `inkPenOnly` preference), a finger
  tap or a 450 ms stationary hold selects the nearest stroke (10 CSS px hit
  tolerance, topmost wins). A mouse click in Hand mode and a short tap with
  the lasso select the same way. A swipe still scrolls: more than 8 CSS px
  of movement, a second contact, a pen contact or a cancel drops the pending
  selection. With finger drawing on, armed tools draw at once; use Hand to
  tap-select.
- The **selection menu** opens after a tap or a lasso ("Edit
  handwriting"), up to two rows of 36 px buttons. The first row never
  changes: Color, Width, Style (greyed, "Pen styles arrive in a later
  release", until the brushes land), Copy, Duplicate, More and Delete.
  **More** (⋯, `aria-expanded`) adds the second row, "More edits": Cut,
  Flip horizontal, Flip vertical, New note, Merge into note (only when
  the selection is one whole group and the page has another), Select note
  (every stroke of the selected ink blocks) and Show note (jump to the
  notes pane) — at most seven buttons in a row, a hard cap for every later
  addition. Whether the second row is open is remembered for the session.
  Color and Width open their option row under whichever rows show, one at
  a time. Edits apply at once and keep pressure and time; a mixed
  pen/highlighter selection gets a width row per kind. Duplicate offsets
  fresh-id copies by 12 screen pixels and selects them; a full group
  refuses. Flip mirrors the strokes about the selection box's centre,
  widths and timing untouched, the box staying put; a second flip puts
  them back. One action across several blocks is one undo entry, and
  every action works without a keyboard; a button's title names the chord
  where one exists ("Copy · Ctrl+C"). The menu follows scrolling and
  resizing, flips above or below the selection and hides while the
  selection is off-screen. It keeps inside the scroller the ink is seen
  through: the PDF viewer, the notebook view, or the notes for a sheet
  among them, and below the strip when the strip floats over the top of
  that scroller; its tallest state (two rows and a colour row) fits a
  740 × 620 viewport. A text box's menu is placed the same way
  (`useSelectionMenuAnchor`). A blank tap or Escape dismisses it.
- **Copy, cut and paste.** Copy (Ctrl+C, or the menu) keeps the selected
  strokes on a handwriting clipboard — in memory for this tab, and as a
  best-effort system clipboard item for other apps ("Client" below) —
  and the pill says "Copied 3 strokes." with *Paste* beside it, the way
  to paste without a keyboard. Cut (Ctrl+X) copies and deletes, "Cut 3
  strokes." with *Paste*. Paste puts down fresh-id copies, keeping each
  stroke's pressure, timing and baked width and dropping a piece's
  `from`, with the copied box's top-left at the target and the box kept
  inside the page; a different kind of surface (a sheet from a PDF page,
  or back) takes them at the same point size. Three ways to say where:
  **Ctrl+V** (or the pill, or More → Paste handwriting) centres the
  copied box on the page nearest the viewport's centre (the sheet in view
  on a notebook); a **pen or mouse held still** on the page for 600 ms with
  a writing tool armed and something on the clipboard, before it has
  drawn 2 css px, opens a one-row menu, *Paste here*, at the point; a
  **right-click** on blank page with a tool armed opens the same row.
  Fingers never open it: a resting palm must not paste. The pasted
  strokes join the page's current group as one undo entry, "ink paste",
  and become the selection with the lasso armed. Select all (Ctrl+A, or
  More) selects every stroke on that page and arms the lasso. Duplicate
  is Ctrl+D as well.
- **New note from selection** (More → New note) moves the selected
  strokes out of their blocks into a new ink block on the same page,
  their ids kept, as one undo entry ("ink split"); a selection that is
  already a whole note of its own is left as it is ("This handwriting is
  already a note of its own."). **Merge into note** (shown when the
  selection is one whole group and the page has another) appends this
  group's strokes to the page's previous group — the nearest one before it
  in the page's order, else the first after it — and empties
  this one, whose block then goes when it holds no caption or notes; one
  that does is left as it is and the pill says "Not merged: this note has
  a caption or notes under it." Undo brings both groups and the block
  back together.
- A **finger drag inside the selection** moves it, even in pen-only mode
  (the dashed box is a `touch-action: none` hit surface; fingers outside it
  scroll). A pen with a writing tool clears the selection and writes. The
  box has a bottom-right **resize** handle and a top-right **rotate**
  handle for mouse, pen and touch: both act around the selection centre,
  preview while dragging and commit one undo entry on release; resize keeps
  proportions and scales the stroke width, rotate does not. Shift snaps
  rotation to 15°; a focused handle takes arrow keys (10% / 15°). The
  handles keep their screen size and follow the live preview. Pressure,
  timing, tilt and stroke ids are untouched.
- **Hover feedback:** a mouse or hovering pen shows the tool's footprint
  around a centre mark. Pen and highlighter widths follow the zoom; the
  eraser radius stays in screen pixels, like the erasure. Tool and menu
  buttons show their description on hover or keyboard focus.
- **Undo / Redo buttons** on the strip (*Undo ink* / *Redo ink*) step the
  stroke history; their disabled state follows it and resets on leaving
  the page. Selecting alone is not an entry.
  - While the Text tool is armed or a text box is selected or edited, they
    read *Undo* / *Redo* and step the page's block history. They stay
    enabled, since the block history keeps no count, and a step with
    nothing to undo says "Nothing to undo in notes."
  - A press on them never takes the focus from an open editor, a box's or
    a note's. In the block-history mode the step is then that editor's, as
    Ctrl+Z in it would be.
  - **Undo with fingers** (`inkFingerUndo`, this device's: Off · Tap ·
    Double tap; Double tap by default on a touch screen, Off elsewhere):
    two fingers tapped together undo, three redo, routed exactly as the
    strip's buttons are (the stroke history, or the block history while
    the Text tool or a text box has it). A chord is two or three touches
    that land within 80 ms, move under 10 css px and all lift within
    250 ms; a pinch or a scroll cancels its touches rather than lifting
    them, so it never undoes, and touches while a pen is down are a palm.
    It works wherever handwriting can be drawn, strip open or not; with
    nothing to undo it does nothing. The first chord of a session says
    "Two fingers undid a stroke." with a *Turn off* action.
- **Settings** for handwriting sit under Reading & editing in two
  sections ([settings.md](settings.md) "Reading & editing"):
  **Handwriting**, the account's — *Reset presets* (with a confirm),
  *Eraser returns to the last tool*, *Erases*, *Draw and hold*, *Scribble
  to erase*; the presets themselves (`inkTools`) are the account's too,
  though they are edited on the strip — and **This device**, this
  browser's — *Draws with*, *Stylus draws right away*,
  *Pressure-sensitive strokes*, *Undo with fingers*. Presets, smoothing
  and the straight highlighter are set on the strip; More → *Handwriting
  settings…* opens the section.
- **A stylus draws right away** even with the strip closed (Settings →
  Reading & editing → This device; on by default), with the last pen
  preset armed on the strip. **Fingers never draw**
  when *Draws with* is *Pen only* (`inkPenOnly`, default on touch screens): they keep
  scrolling and pinch-zooming. The first finger that scrolls a page while
  a tool is armed says so on the status pill, "Fingers scroll while a pen
  draws", with **Draw with finger** (which turns `inkPenOnly` off): once a
  session, and never after a pen has drawn. The pen's eraser end and
  barrel button erase.
- Strokes on one page join the **current group** until a stroke on
  another page, or leaving the page. A group is one block in the
  notes: a rounded pen marker, the strokes as a picture, and the block's
  text as its caption (children allowed). The marker or the card scrolls
  the PDF to the group and outlines it briefly; a click on ink selects it
  (Show note scrolls the notes to its block), and in read-only views jumps
  to the block directly.
- **Replay.** The play button in the card's corner (on hover; always on a
  touch screen) replays the group's writing on its page, scrolled into
  view (the PDF page, the notebook view's sheet, or the sheet in the
  notes), and in the card: the strokes appear in the order they were
  written, each at the pace it was written. Pauses shrink to 0.4 s, and a
  replay longer than 15 s plays faster. The same button stops it, and one
  replay at a time plays. A sheet in the notes view has the button under
  it, for all its handwriting ([notebooks.md](notebooks.md)); on the iPad
  it is on a group's row in the notes and plays on the page
  ([ipad.md](ipad.md)). A replay shows the drawing as it is now: erased
  strokes are gone, and the pieces the partial eraser left keep the time
  they were written. A recording made while writing replays the same
  strokes in step with its audio ([recording.md](recording.md)): the
  replay button and the recording's player run on one store.
- **Transcribe with AI**, in a group's ⋮⋮ menu, attaches the block to the
  chat and asks for its text in the caption ([ai_tools.md](ai_tools.md)
  "view_ink"). The chat's "Attach what I see" sends the visible page with
  the handwriting on it, and the agent's `clip_region` stores a picture of
  a group for the notes ([ai.md](ai.md#pictures)).
- A group erased empty deletes its block, and undo brings it back. A
  group whose block holds a caption or notes keeps its block, with an
  empty drawing, so erasing strokes never deletes text.
- Read-only views (workspace viewers, view shares) show ink without tools;
  edit shares draw.
- On an iPad this layer runs in Safari or the web app installed to the
  home screen. The iPad app captures the Pencil natively and writes the
  same files ([ipad.md](ipad.md) "The pen").

## Model

A stroke is a tool, a look and a polyline of samples. Two tools are
known, `pen` (a filled outline) and `highlighter` (a stroked polyline,
multiplied onto the page and drawn under the ink); one brush, `monoline`
(a pen of constant width). Both are open strings in the file, so a later
pen style is a new value and a renderer that does not know it draws the
stroke plainly from its baked width (below, "Width"). Pressure, timing and
tilt stay in the samples, raw; the width a pressure pen drew with is baked
into the samples once, when the stroke is finished, as a multiplier of the
stroke's nominal size. Live canvas, retained SVG, eraser geometry, hit
tests and the server's exporters all draw from that baked width — there is
no width formula in any of them — and partial erasing keeps it in every
surviving piece. The storage shape is a deliberate copy of what the two
reference apps store ([research/ink-storage.md](../research/ink-storage.md)):
Notability's base width times a per-anchor fractional width with the raw
force and tilt beside it, GoodNotes' explicit highlighter flag and
parametric shape records.

An ink group is a block with these properties (no schema change):

| key | value |
|---|---|
| `ink_url` | `/api/uploads/<hash>.ink`, the group's stroke file; `""` for the moment between the first stroke and its upload |
| `pdf_position` | on a PDF page, the group's bounding box in the highlight shape (`{pageNumber, width, height, boundingRect: {x1, y1, x2, y2}, rects: [the box]}`, [api.md](api.md) "The highlight shape"): the page and the file's `space` size once, the box in its points. Jump-to-position, markers and the exporters treat ink like any region, and the page's layer finds its groups by `pageNumber`. A group made before its first upload carries its draft's box. None on a sheet |
| `ink_strokes` | stroke count |
| `imported_annot` / `annot_stripped` | as on highlights, for ink that came from the PDF's own `/Ink` annotations |

Why a file, not strokes in the properties: a page's tree is sent whole on
open and every property change is an op-log row and a socket message
carrying the full value; a paper's handwriting is hundreds of kB. As an
upload it behaves like a pasted image — the tree carries a URL, the viewer
fetches files per page, and orphan cleanup, share-scoped serving, quota,
the export bundlers and backups already understand `/api/uploads/`
references in properties.

## The stroke file (`gamma-ink` v2)

Plain JSON (`application/json`), one per group:

```json
{"format": "gamma-ink", "version": 2,
 "space": {"kind": "pdf-page", "page": 3, "width": 612, "height": 792},
 "strokes": [{"id": "k7Qm2x", "tool": "pen", "color": "#1f1f1f", "size": 1.6,
              "opacity": 1, "pen": true, "t0": 1757760000000,
              "ch": "xyptw", "pts": [12040, 30512, 620, 0, 1120, 18, -3, 700, 8, 80]}]}
```

**The file.** `format` is the literal `gamma-ink`; `version` is `2`
(version 1 is what every file was before release T; "Upgrading version 1"
below). `space` is where the strokes lie: `kind` is `pdf-page` or `canvas`
(a literal; a third kind would be a version 3), `page` 1-based (required
for a PDF page), `width`/`height` the page as displayed at scale 1 (pdf.js
viewport: points, origin top-left, y down, rotation applied — the frame
highlight rects normalise to and the frame the PDF writers map to user
space); `{kind: "canvas", width, height}` is a sheet of paper, points from
its top-left corner, the sheet's size when the group was drawn
([notebooks.md](notebooks.md)). `strokes` is the list below. Any other key
of the file or of `space` is kept verbatim by every reader and writer
(parse, canonical bytes, merge, the exporters' `/GammaInk`), up to 16 KB of
serialised extras per file; on a merge the result takes the writer's
file-level extras (last writer by strokes, advisory only, since nothing
writes one yet).

**A stroke.** Every key but `id` and `pts` has a default or is omitted,
and the canonical bytes never write a key at its omission value:

| key | type | default / omitted when | meaning |
|---|---|---|---|
| `id` | `^[A-Za-z0-9_-]{1,32}$` | required; a client mints 8 chars | survives every edit (the merge's handle) |
| `tool` | `^[a-z][a-z0-9-]{0,15}$` | `pen` | `pen` or `highlighter` are known; an unknown value draws as a round-capped polyline of the baked width, source-over at `opacity`, in its own export bucket; the eraser and the lasso take it like any stroke |
| `brush` | `^[a-z][a-z0-9-]{0,15}$` | omitted | `monoline` is known (constant width, no `w`); an unknown brush draws exactly as a pen from `w`, so only texture is lost. Open on every tool: the server does not refuse one on a highlighter (it draws at its size, as any highlighter) |
| `color` | `#rrggbb` or `rgb[a](…)`, ≤ 40 chars | `#1f1f1f` | |
| `size` | number in (0, 100] | `1.6` | nominal diameter in pt, the anchor `w` multiplies; a width restyle changes `size` only, a resize scales it; the server refuses `size × max(w) > 100` |
| `opacity` | number in (0, 1] | `1` | |
| `pen` | bool | `true` | `false` marks a mouse or finger stroke (no real pressure, drawn even, no `w`) |
| `t0` | int ≥ 0 | omitted | wall-clock ms of the first sample, never invented (a client without timing omits `t` and `t0`); the replay reads both |
| `band` | `0` or `1` | the tool's default, `0` for a highlighter and `1` otherwise, and omitted when equal to it | `0` draws under, `1` draws as ink. Every layer draws band 0 first, then band 1 (`InkLayer`, `to_svg`, `pdf_path_ops`, the annotated PDF's `/Annots` order); a tap prefers band 1 within the radius (`nearestInkStroke`) |
| `from` | a stroke id | omitted | the stroke this piece was cut from by the partial eraser. A cut of a piece keeps the root's id, so every piece names the original. Written by `restroke`, kept by every edit, dropped on paste and duplicate (`cloneStrokes`, `duplicateStrokes`: a copy is not a piece). Read by the merge only |
| `shape` | `{kind, params}` | omitted | `kind` one of `line` `arrow` `rect` `ellipse` `triangle` `polygon` `curve`; `params` an object of at most 64 finite numbers (and booleans), the geometry an editor rebuilds `pts` from after a handle drag. The stroke **also carries its baked `pts`**, so hit test, bounds, eraser, lasso, replay and every exporter draw it as any stroke; a piece cut from a shape loses `shape` and `fill` |
| `fill` | `{color: '#rrggbb', opacity}` | omitted; `opacity` in (0, 1], omitted at `0.25` | closed shapes only |
| `dash` | `dashed` or `dotted` | omitted (solid) | dash geometry is derived from `size` at render time, never stored |
| `nib` | int 0..179 | omitted | a calligraphy nib's angle in degrees |
| `ch` | `^xy[a-z]*$`, no repeated letter, ≤ 12 chars | `xy` | the channels of each sample, below |
| `pts` | non-empty list of ints, a multiple of `len(ch)` long | required | the samples, flat |
| anything else | JSON to depth 3, finite numbers, keys `^[A-Za-z][A-Za-z0-9_]{0,31}$`, strings ≤ 1 KB, arrays ≤ 256 | — | kept verbatim through parse, canonical bytes, merge and every edit helper (`restroke`, `duplicateStrokes`, `cloneStrokes`, `translateStrokes`, `transformStrokes`, `restyleStrokes`); ≤ 1 KB serialised per stroke. A null-valued key is dropped |

The server's models are pydantic with `extra="allow"` and one extras
validator for the caps (`gamma/ink.py`); `json.loads` runs with a
`parse_constant` that refuses `NaN` and `Infinity`, and `dumps` with
`allow_nan=False`, because pydantic would keep a `NaN` extra and
`JSON.parse` in every browser then fails on the file.

**Channels.** `ch` names the channels of each sample, InkML-style, `x`
`y` first and then any of the letters below in any order; `pts` is one
flat integer array, `len(ch)` integers per sample, ~300 bytes a stroke.

| letter | unit | encoding |
|---|---|---|
| `x` `y` | 1/100 pt | delta after the first sample |
| `p` | pressure 0..1000 | absolute; decoded as `p / 1000` and clamped to 0..1 |
| `t` | ms since `t0` | delta after the first sample |
| `a` | altitude, degrees 0..90 | absolute |
| `z` | azimuth, degrees 0..359 | absolute |
| `w` | drawn-width multiplier × 1000 | delta after the first sample; the running sum is checked to 0..2000 (decoded 0..2.000, `1000` = the nominal size) |
| `o` | drawn opacity × 1000 | delta; running sum 0..1000; **reserved**: written by nothing yet, read as `opacity × o` once a brush writes it |

A letter neither codec knows is carried by stride: `decodeStroke` /
`decode_stroke` put its integer in the sample's `rest` array, every reader
ignores it, and both encoders copy `rest` back in place, so a cut on a copy
that predates a letter keeps the stride and the file stays valid. The
decoded sample is `{x, y, p?, t?, a?, z?, w?, o?, rest?}` — each key
present when its channel is, `rest` when the list has a letter the codec
does not know — with `w` and `o` as multipliers, so a decoded cache never
depends on `size`; `tests/shared/inkwidth.json` pins the decoded values.

**Width.** The drawn width at a sample is `size × w` when the stroke has
the `w` channel, else `size`. That is the whole rule: there is no width
formula in rendering, hit-testing, bounds, the exporters or the server
(`strokeWidth(stroke, sample)` in `ink.js`, `stroke_width(stroke, sample)`
in `ink.py`, both one line). Pressure-shaped strokes — tool `pen`, `pen`
true, no `brush`, and later every pressure brush — always carry `w`, baked
once on the client in `finishStroke.js` `bakeWidths`, the only place a
brush law lives: the default pen's law is `w = 1 + (p − 0.5)`,
perfect-freehand's on-screen value at thinning 0.5, capped to `[0, 2]`.
Constant-width strokes (highlighter, monoline, `pen: false`) omit `w`.
The raw `p`, `a`, `z` stay beside `w` (Notability keeps force and tilt
beside its fractional widths), so a brush change re-bakes from them and a
future law can re-map; the multiplier form means a width restyle touches
`size` alone and `pts` stay byte-identical. perfect-freehand is driven from
`w`: pressure = `w / 2` with thinning 1, so the outline's radius is
`size × w / 2` and matches the baked width exactly; the live canvas runs
the same function on the samples' live `w`, computed by the same law. The
iPad shell sends raw samples and the web app bakes them like its own
([ipad.md](ipad.md) "The pen").

**Limits** (`gamma/ink.py`, enforced on upload): 5 000 strokes, 500 000
samples, `MAX_BYTES` 6 MB, finite numbers, unique stroke ids, the extras
caps, `size × max(w) ≤ 100`.

**The bytes of an upload** are the client's: `ink.js serializeInk` — keys
sorted at every level, no whitespace, null and undefined keys dropped,
whole-number floats written as integers (`612`, never `612.0`). The server
validates an upload and stores it as it came, named by the sha256 of those
bytes (`routers/ink.py`, `storage.store_file`), so the same strokes from a
browser, the iPad and the migration are one file, and a client can name a
file by its hash before the server has it. The server writes files of its
own with `ink.dumps` (the same canon, pinned equal to `serializeInk` on a
fixture, non-ASCII text in an extra key written as UTF-8 on both sides,
never `\u`-escaped) for a merge, an import or the upgrade step. The codec lives twice
by design (`ink.py` `decode_stroke` / `encode_points`, `ink.js`
`decodeStroke` / `encodeStroke`); `tests/shared/inkv2.json`,
`inkwidth.json` and `inkmerge.json` pin both sides to the same bytes.

**The byte budget** is the client's to keep: `applyInk` measures the
group's bytes (`inkBytes`: exactly `serializeInk(ink).length`, summed
from each stroke's canonical bytes and cached per stroke object, so an
edit costs its changed strokes only) and refuses an edit that would
cross `MAX_BYTES` with the pill "This group is full; split it or start a
new one", and a new stroke that would push a group past 3 MB starts a new
group block on the same surface instead (a group is "the page's current
writing", so this is invisible). `finishStroke` decimates coalesced
samples closer than 0.1 pt to the previous kept one before baking, so a
240 Hz trace roughly halves with no visible change.

### Upgrading version 1

Release T upgraded every stored file once; nothing reads version 1 after
it except one pure converter, `ink.upgrade_v1` (Python) and `upgradeV1`
(JavaScript), deterministic and pinned byte for byte by
`tests/shared/inkv2.json` (v1 input bytes and the exact v2 output). The
law: `version` becomes 2; for every stroke whose tool is `pen` (or
absent), `pen` is not `false`, `brush` is absent and `ch` has `p` but no
`w`, `w` is appended to `ch` and each sample gains
`w = 500 + clamp(p, 0, 1000)` — the multiplier `1 + (p / 1000 − 0.5)`
times 1000 — absolute on the first sample and a delta after it; every
other stroke (no `p`, monoline, highlighter, `pen: false`) and every other
key is unchanged; a file without a `version` counts as 1; the output is
the canon above. Anything that is not version 1 (version 2, a newer
version, a malformed one) comes back as it is, so the converter can never
rewrite a newer file down to 2; `parse_ink` judges it. The
old law `size × (1 + 0.5 × (p − 0.5))` is gone from both languages: it was
never what the screen drew (perfect-freehand at thinning 0.5 draws
`1 + (p − 0.5)`), so a stroke drawn across the upgrade exports as it
looked on screen, slightly heavier than the old exports.

The converter runs in four places and nowhere else:

- **Workspace migration step 40 `ink_v2`** (`migrations.WORKSPACE_STEPS`,
  `db.SCHEMA_VERSION` 40, so `workspace_format()` moves and a mirror
  pair pauses until both sides run it, [mirror.md](mirror.md) "The data
  format"): for every block whose properties carry `ink_url` →
  `/api/uploads/<sha>.ink`, read the file from the workspace's uploads;
  a version 1 (or unversioned) file is converted, its `ink.dumps` bytes
  written as a **new** upload named by their sha256 (the same naming as
  `POST /api/upload-ink`), and the block's `ink_url` set to the new name
  with `ink_strokes` and `pdf_position` untouched; the old file stays for
  the orphan cleanup; a v2 file is skipped, so the step is re-runnable; a
  file that cannot be read or converted is logged and its block left as
  it is. Because the conversion is pure, every copy of a workspace — the
  server, a desktop mirror, the iPad — produces the same hashes, and the
  mirror's digest sync agrees once both have run it. It is the first step
  that writes upload files, a documented exception to the migration rule
  that uploads are never copied ([migrations.md](migrations.md) rule 4 and
  step 40). `MAX_BYTES` moves from 4 to 6 MB with it.
- **`POST /api/upload-ink`** and the mirror's `POST /api/upload-file`
  refuse version 1 with `400 {detail: "update Gamma"}`: after the step
  every writer is v2.
- **The PDF importer** (`imports.py`, a `/GammaInk` payload inside an
  annotated PDF exported before the upgrade) converts before it stores;
  exports write the v2 strokes verbatim into `/GammaInk`.
- **Backup restore** runs the step like any other (migration rule 7), so
  a zip made before the upgrade comes back converted; `ws_backup` refuses
  a zip stamped above `SCHEMA_VERSION`.
- **`inkStore.loadInk`** on the client converts a v1 file it is served
  (a copy that has not run the step yet) so it renders, and never
  re-uploads it as v1: the next edit's upload is v2 and the stored file
  is replaced by that write.

## Two writers, one group

A group's drawing merges like a block's text ([collab.md](collab.md)
"same-block merge"), with strokes in place of spans. Stroke ids survive
every edit, so they play the part text offsets play. A write names the
file its strokes were drawn onto (`base_props: {ink_url}` on the op,
`base_properties` on `PUT /blocks/{id}`) and may carry `base_ids`, the
stroke ids of that file as the client loaded it. When the stored `ink_url`
is another one, meaning someone else saved meanwhile, the server merges
instead of replacing (`ops._Batch.merge_ink`, `ink.merge_ink`; the client's
`mergeInk` is the same function, `tests/shared/inkmerge.json` pins both):

- A stroke is compared as two halves. Its **geometry** is `pts`, `ch`,
  `size` and `shape`; its **look** is everything else but `id` and `from`:
  `color`, `opacity`, `brush`, `dash`, `nib`, `band`, `fill`, `tool`,
  `pen`, `t0` and any extra key. A width restyle is therefore geometry, a
  brush change (which re-bakes `w`) changes both halves, a move or a
  resize geometry only.
- Per id, three ways: a half changed by one side only is taken from that
  side, so one writer's move and another's recolour compose; a half
  changed by both (to different values, on any of its keys) takes the
  stored (theirs) version and marks the merge unclean. A stroke neither
  side touched is the identical object.
- A stroke the writer added goes after the stroke before it that the
  result keeps, behind strokes the other side added there (theirs comes
  first, as with text). One id added on both sides with different
  content keeps theirs, unclean.
- An edit beats a delete: a stroke one side erased and the other changed
  (either half) stays, unclean. Both erasing it is clean.
- **Pieces follow their parent.** A stroke with `from: X` that one side
  added (its *parent* is the stroke of X's family — X or a piece naming
  X — that this side no longer has) follows the parent's fate on the other
  side: the parent unchanged there, the pieces stand and the parent goes;
  its look changed there, the pieces take that look (and when the cutting
  side had restyled them too, theirs' look wins, unclean); the parent
  erased there, the pieces are erased too (an undo of the drawing wins
  over a cut of it); its geometry changed there, or cut there as well,
  theirs' version — the moved stroke, or theirs' pieces — stands and the
  writer's pieces are dropped, unclean. So a cut against a restyle never
  draws the ink twice, and a cut against an undo leaves nothing behind.
  A piece whose parent the base never had is an ordinary addition. A
  copy that writes no `from` (an older release T copy) gets the old
  rule: its pieces are additions and its cut an erasure.
- **A base file that is gone** merges by `base_ids` when the write
  carries them: a stroke one side lacks whose id the base had is treated
  as erased — deletions win both ways, since "the other side changed it"
  cannot be judged without the base's content — and the merge is marked
  unclean when such a deletion was applied; a stroke the base did not
  have is an addition and stays. Without `base_ids` it is the old union by
  id. The mirror avoids this mode by pushing the base file an op names
  when the origin lacks it. A file that cannot be read, or a result over
  the budgets, leaves the write as sent (last writer wins).

The merge returns the file, `clean`, and two id lists: `lost_ours`, the
writer's changes the result dropped (an edit or erasure theirs overrode,
pieces that were dropped), and `returned`, the strokes theirs had erased
that the writer's change brings back. `ops.merge_ink` puts
`{clean, lost_ours, returned}` into the batch result — not the echo, so
the op log, catch-up and mirrors never carry it — and `routers/blocks.py`
copies it onto the `PUT` answer as `ink_conflict`. The writer's pill says
"Your change to n strokes was not applied: someone changed them since"
for `lost_ours`, "n strokes someone erased came back" for `returned`, and
"Merged with changes made meanwhile" for an unclean answer with neither.
Undo and redo collect the same flag (`inkUndo`): "Undone: … (n strokes
someone changed since were kept)".

The merged file is stored (`ink.dumps`) only when it is new. One stored
already stays: the purge deletes only under the write lock the batch
holds. The applied op names the merged file, with its stroke count and
box. Merging a change that already landed adds nothing, so a mirror
resends a lost ink push as it is (sync_engine `_unlanded`). A merge that
changes nothing returns the side it equals (the space compared on its
known keys), so a resend mints no copy.

The same function runs where a client applies another side's drawing
itself:

- the desktop mirror, through its local server's op path (its diffs
  carry `base_props`, sync_tree `_set_op`); a `400` on an `.ink` push is
  recorded as a per-file `sync_conflicts` row and the page's other ops
  land (sync_engine `_push_files`);
- undo and redo in the browser and on the iPad, which apply an entry's
  change onto the group as it is now, so strokes someone else drew
  meanwhile stay.

Keys other than `ink_url` stay last-writer-wins, except a text box's
`text_box`, merged key by key ([text_boxes.md](text_boxes.md) "Merge").

**Fences** — code, not data, for the copies the format pause does not
reach:

- `flushInk` keeps the 700 ms debounce, adds a 5 s maximum interval
  while writing goes on (so files land during a session), uses `fetch`
  with `keepalive` on `pagehide` — the upload and the block's `PUT` go
  side by side then, the `PUT` naming the file by the hash `inkStore`
  worked out when the draft changed (`nameLater`, idle time), since
  nothing is sure to run once the page is gone; a file over
  `KEEPALIVE_BYTES` (56 KiB, under the browsers' 64 KiB in-flight cap)
  goes as a plain fetch instead, which may still land; and a draft the
  server will have to merge (its block names another file than the
  draft's base) keeps the upload-then-`PUT` order — and **stops retrying
  on a 4xx**
  (`collabSession.retryableStatus` decides): the draft is kept and a
  persistent pill says "Handwriting not saved: <detail>". 5xx and network
  errors keep the 2 s retry.
- The page socket's hello and `whoami` carry the server's `build`; the
  client compares it with its own at connect and on every reconnect and
  on a mismatch shows "Gamma was updated, reload to keep drawing" and
  refuses to start a stroke or a cut (reads keep working). A tab left
  open across a server update, or the web app on an iPad home screen,
  runs the old bundle until reloaded.
- `ws_backup` refuses restoring a zip stamped above `SCHEMA_VERSION`;
  `sync_engine` records a `400` on an `.ink` push per file instead of
  failing the page; the byte budget and the decimation above.

## Client

- `ink/ink.js` (pure): the codec (`encodePoints` / `encodeStroke`,
  `decodeStroke`, the known letters in `KNOWN_CHANNELS`), `strokeWidth`,
  `strokeBand` / `byBand` (a drawing in band order), bounds (`strokeBounds`,
  `inkBounds`, `boundsOf`, `unionBox`), `pdfPositionOf`, the stroke edits,
  the merge and the rendering. `hitStrokes` is the whole-stroke eraser's
  test; `eraseAt` the partial eraser, which re-encodes the surviving runs
  as new strokes; `translateStrokes` only touches the first sample's two
  absolute integers; `transformStrokes` scales/rotates selected XY samples
  around a shared origin without changing the other encoded channels;
  `strokesInLasso` picks strokes with more than half their samples inside
  the polygon. A pen stroke (any brush) renders as perfect-freehand's
  outline in one filled SVG path (page units; the layer's `viewBox` does
  the zoom), driven by the baked `w` (`outlineOptions`, `outlinePressure`);
  a highlighter as a stroked polyline with `mix-blend-mode: multiply`; an
  unknown tool as a source-over polyline at its mean drawn width
  (`strokePath`, with `dashArray` from `dash`). Paths, decoded samples and
  bounds are cached per stroke object; `inkStore.adoptStrokes` keeps a
  reloaded file's unchanged strokes as the objects already drawn, so a
  merge's result reuses their outlines.
  `nearestInkStroke` resolves a touch to the nearest stroke edge (a band-1
  stroke wins over a band-0 one within the radius, then the topmost);
  `restyleStrokes` changes selected colour or `size` (never `pts`),
  returning the original object for a no-op; `cloneStrokes(strokes, dx,
  dy, used?)` is the copy every paste and duplicate is made of: fresh ids
  (none in `used`), `from` dropped, moved by `dx, dy` pt through the
  first sample's two absolutes, every other key — `t0`, `p`, `t`, `w`,
  `shape`, extras — kept; `duplicateStrokes` is it on a selection.
  `appendStrokes(ink, strokes)` adds strokes to a group as they are (a
  paste's copies, a merge's moved strokes, `from` and all), re-minting
  only an id the group already holds, → `{ink, ids}` as they landed.
  `flipStrokes(ink, ids, {cx, cy, axis})` mirrors the selected strokes
  about `(cx, cy)` — `'h'` rewrites each `x` as `2cx − x`, `'v'` each `y`
  as `2cy − y` — in the samples alone (size, `p`, `t`, `w`, ids and
  channels stay), carries a `shape` record through the same reflection
  (points and centres reflected, an angle `180° − angle` for a horizontal
  flip and `−angle` for a vertical one, re-normalised as `recognise`
  returns them) and a `nib` as `(180 − nib) mod 180` (the same on either
  flip: an undirected angle reflects the same way about both axes), and
  returns the same object when nothing changes; two flips are the
  identity, a unit test pins it. Every helper spreads
  the stroke object, so unknown keys ride along; `restroke` (the partial
  eraser's piece) keeps every key but `shape` and `fill`, mints an id and
  writes `from`. `withDefaults` fills a loaded file's omitted stroke keys
  (`STROKE_DEFAULTS`), and the helpers tolerate them anyway.
  `upgradeV1` is the version 1 converter ("Upgrading version 1");
  `inkFeatures(ink)` lists what a drawing carries that this build does not
  know (tools, brushes, keys, letters, a newer version), null when
  nothing; `inkBytes` / `strokeBytes` give a group's exact upload size,
  cached per object, for the byte budget (`MAX_BYTES`,
  `GROUP_SPLIT_BYTES`).
- `ink/finishStroke.js` (pure): `finishStroke(samples, ctx) → {commit,
  erase, label, style, status}`, the one stage between a lifted pen and
  the committed stroke. `InkLayer`'s finish and the iPad `claim.commit`
  both call it, and the live canvas draws the stroke in progress with the
  same `widthLaw`, so the committed look matches the line under the pen.
  - `samples`: the stroke's decoded samples in page points, `[{x, y, p?,
    t}]`, `t` ms since the stroke's first sample, **already stabilised**
    (the stabilizer runs as the samples arrive, below), so this function
    sees the same points the live canvas drew.
  - `ctx`: `{style, pen, k, hold, straight, scribble, groups, keep}` —
    `style` `{tool, brush, color, size, opacity}` the preset's look;
    `pen` a stylus drew it; `k` css px per pt (the layer's scale, for
    thresholds given in screen pixels); `hold` the layer's live hold
    result at the lift, `{kind, params} | null`; `straight` the
    highlighter preset's `'hold' | 'always' | 'off'` (`undefined` reads
    as `'hold'`); `scribble` whether scribble-to-erase is on; `groups` the
    page's groups, `[{id, ink}]`, and `keep(stroke) → bool` the eraser
    filter, both for the scribble stage. Every key but `style` is
    optional, so the existing calls stand.
  - The stages, in this order, each returning as soon as it decides:
    1. **hold**: `ctx.hold` set → `commit = shapeSamples(kind, params,
       {span: last t − first t, p: mean p})` (page units, `t` from 0 over
       the span), `style = {...ctx.style, pen: false, shape: {kind,
       params}}` (an even line in the preset's colour and width; the
       `shape` key rides into `encodeStroke` as an extra; a `circle` is
       written as `{kind: 'ellipse', params: {cx, cy, rx: r, ry: r, angle:
       0}}`, since the file's kinds have no circle and the server's
       `SHAPE_KINDS` is not touched in this milestone — `circle` stays the
       recogniser's answer for the pill and the refit), `label`
       `T("ink straight line")` for a line or arrow, `T("ink shape")`
       otherwise, `status` `{text: t("Straightened.") | t("Shape: {kind}.")
       (kind named through `shapeName(kind)`, a t() per kind), once:
       false}`.
    2. **straight highlighter**: `style.tool === 'highlighter'`,
       `straight === 'always'`, no hold result → when every sample lies
       within `min(0.75 × size, 6)` pt of the chord (first to last sample)
       and the chord is `≥ 4 × size` long, `commit` is the two-sample
       chord, snapped horizontal when within 4°, `t` 0 and the span,
       `style = {...ctx.style, pen: false, shape: {kind: 'line', params:
       {x1, y1, x2, y2}}}`, `label` `T("ink straight line")`, `status`
       `{text: t("Straightened."), once: true}`; otherwise the stroke
       goes on to the stages below unchanged. (`'hold'` is the hold stage
       with lines only, which the layer enforces by asking `recognise`
       for lines alone; `'off'` disarms the hold timer for that preset.)
    3. **scribble**: `ctx.scribble`, `style.tool === 'pen'` (any brush),
       no hold result → `isScribble(samples, {k})` and then `targets =
       scribbleTargets(samples, groups, {keep})`; with at least one target
       `{commit: null, erase: targets, label: T("ink scribble erase"),
       style: ctx.style, status: null}` (the layer's `onEraseMany` raises
       the pill); with none, or when it is not a scribble, the stroke goes
       on as ink.
    4. **decimate** (`DECIMATE_PT`, 0.1 pt) then **bakeWidths** (the brush
       width table, `widthLaw(style)`, the only place a width law lives;
       `strokeChannels(style)` is the `ch` a new stroke is written with,
       `xyptw` for a pressure pen, `xypt` for a monoline or a highlighter,
       `xyt` for a mouse or finger stroke and for every snapped result,
       since those are `pen: false`): `{commit, erase: null, label: T("ink
       stroke"), style: ctx.style, status: null}`.
  - The result: `commit` the samples to encode (`null` for nothing),
    `erase` `[{gid, ids}] | null`, `label` the history entry's, `style`
    the object to encode with (`ctx.style` itself when nothing snapped),
    `status` `{text, once} | null` — a pill for App's `undoableStatus`,
    `once` meaning once a session. The caller encodes with
    `encodeStroke({...style, t0, samples: commit, ch:
    strokeChannels(style)})`, erases through `onEraseMany`, and hands
    `status` to `onHoldStatus`.
- `ink/shapes.js` (pure; fixture `tests/shared/inkshapes.json`, whose
  `about` pins the parameter conventions): the draw-and-hold recogniser.
  - `recognise(samples, {k, kinds}) → {kind, params} | null` over decoded
    page-pt samples `[{x, y, p?, t?}]` as the layer holds them when the
    timer fires (the still tail included). `kinds` (optional) restricts
    the answer, `['line']` for a highlighter. Null when the chord is under
    `max(24 / k, 18)` pt or there are fewer than 8 samples. Then ShortStraw
    corners (resampled at bboxDiagonal / 40, window 3, threshold 0.95 ×
    median straw; the endpoints are corners; a closed stroke — end within
    10 % of the bounding-box diagonal of the start — merges its two end
    corners): **1 side** → least-squares line through the samples, kept
    only when the RMS distance of the samples to it is ≤ 3 % of the chord
    and the path is ≤ 1.08 × the chord, snapped to 0°, 45° or 90° when
    within 5° by rotating about the midpoint (length kept) → `line {x1,
    y1, x2, y2}` from the first endpoint to the last; a **first side
    followed by two or three sides each ≤ 0.35 × it**, whose far ends lie
    within 0.4 × the shaft of the shaft's end and on both sides of the
    shaft line → `arrow {x1, y1, x2, y2, head}` (`x2, y2` the tip, `head`
    the mean barb length, the shaft snapped as a line); **3 sides,
    closed** → `triangle {x1, y1, x2, y2, x3, y3}` in drawing order from
    the corner nearest the first sample; **4 sides, closed, angles 90° ±
    15°** → `rect {cx, cy, w, h, angle}`, `angle` in [−45°, 45°) and 0
    after an axis snap (within 5°), `w` the side along `angle`, a square
    (sides within 10 %) taking the mean side for both; **5–8 sides,
    closed, convex, sides within 15 % and turns all one way** → `polygon
    {cx, cy, r, n, angle}` (circumradius, side count, direction of the
    first corner in [0, 360 / n)); **no inner corner, closed** → the
    inertia test (Xournal++): with the central moments' eigenvalue ratio ≥
    0.95 and the radial deviation about the centroid ≤ 10 % of the mean
    radius → `circle {cx, cy, r}`; else with the radial deviation about
    the moment ellipse ≤ 12 % → `ellipse {cx, cy, rx, ry, angle}` (`rx ≥
    ry`, `angle` in [0°, 180°)); anything else, every open curve included,
    null. Thresholds in css px divide by `k`; everything else is points.
  - `shapeSamples(kind, params, {span, p}) → [{x, y, p, t}]`: the dense
    polyline a shape commits as — a line two samples; an arrow tail → tip
    → barb → tip → barb; a triangle, rectangle or polygon its corners with
    a sample every 2 pt along each side, closed (the last sample is the
    first again); a circle or ellipse at least 48 samples, closed; `t`
    spread evenly from 0 to `span` (ms), `p` constant.
  - `refitShape(kind, params, {pointer, from, shift}) → params`: the
    shape under a continued hold, computed from the **snap's** params
    every time (never from an already-refit set), `pointer` the tip now
    and `from` the tip at the snap, both in page points: a line or an
    arrow takes `pointer` as its new end (Shift snaps the direction to
    15°); a closed shape scales about its centre by |pointer − centre| /
    |from − centre| and rotates by the angle from `from` to `pointer`
    about the centre (Shift snaps the rotation to 15°); a rectangle or
    triangle keeps its proportions; the result is normalised as
    `recognise` would return it. (`from` is an addition to the planned
    `{pointer, shift}`: without the snap position a pure refit cannot
    tell a scale from a rotation.)
  - `shapeName(kind) → string`: the kind's translated name for the
    "Shape: circle." pill.
- `ink/gestures.js` (pure; fixture `tests/shared/inkscribble.json`): the
  scribble.
  - `isScribble(samples, {k, durationMs}) → bool` over decoded page-pt
    samples (`durationMs` defaults to last `t` − first `t`). True when all
    of: at least **4 reversals along the PCA major axis** of the samples
    (a reversal is an extremum of the samples' projection on that axis
    whose excursion on both sides is at least 4 css px, so jitter never
    counts) each with a **turn over 120°** (the angle between the path
    direction 4 css px before the reversal and 4 css px after it); the
    path lengths between consecutive reversals (the first and last partial
    runs excluded) **within 2× of each other**; the path **≥ 2.5 × the
    bounding-box diagonal**; the box's height **≤ 1.5 × its width or ≤ 40
    css px**; duration **≤ 2000 ms**; mean speed (path × k / duration)
    **≥ 150 css px/s**. A cursive word advances along its major axis and
    hardly reverses on it; hatching's major axis runs across the lines;
    both stay ink.
  - `scribbleTargets(scribble, groups, {keep}) → [{gid, ids}]`: `groups`
    the page's `[{id, ink}]` in order; a stroke is a target when its
    `strokeBounds` box lies at least 70 % by area inside the scribble's
    sample bounding box, its `tool` is not `highlighter`, its box's
    diagonal is at most 3 × the scribble's and `keep(stroke)` is true
    (`keep` defaults to accept-all); the answer lists groups in order with
    ids in file order and leaves out groups with no target.
- `ink/stabilizer.js` (pure): `makeStabilizer(level, {k}) → {push(sample)
  → sample | null, finish(sample) → sample[]}`, the lazy brush behind a
  preset's `stabilize`. `R = [0, 3, 6, 10, 16][level]` css px, divided by
  `k` to points; level 0 (or an unknown level) passes every sample
  through (`push` returns it, `finish` returns `[sample]`). The brush
  starts at the first sample (returned as it is). `push(sample)`: when the
  pointer is within `R` of the brush nothing is emitted (null); otherwise
  the brush moves toward the pointer by the distance beyond `R` and the
  emitted sample is the brush position with the pointer's `p` and `t`
  (and `a`, `z` when present). The **cusp rule**: when the pointer's
  direction (from the previous pointer position) turns more than 90°
  against the direction the brush last moved while the string is taut,
  the previous pointer position is the tip of a cusp; once the pointer
  has retreated at least `R / 2` from it, measured along the brush's
  last move, the brush jumps to the tip and that point is emitted, so a
  corner stays a corner (a smaller retreat is tremor and emits nothing; a
  pointer passing the tip again forgets it, and so does one that leaves
  the tip sideways by more than `R`: a shake across the stroke, which
  the string smooths rather than copies). `finish(sample)` (the lift) returns the catch-up: a pending tip
  first, then at most 8
  samples along the quadratic from the brush to the lift point (control
  point: the brush plus half its last move), spaced about `R / 2`, `p`
  the lift's, `t` spread from the last emitted `t` to the lift's, ending
  with the lift point exactly. Nothing here reads the stroke's style.
- `ink/fingerChords.js` (pure): `makeChordDetector({onChord, mode, now})
  → {down(e), up(e), cancel(e), move(e), setMode(mode), reset()}`, the
  two- and three-finger undo chords. `e` is any object with `pointerId`,
  `pointerType`, `clientX`, `clientY` and `timeStamp` (`now()` is only
  for tests; the events' own `timeStamp` is the clock). `mode` is `'off'
  | 'tap' | 'double'`. A **chord**: N = 2 or 3 `touch` contacts whose
  downs lie within 80 ms of the first, none of which `move`s more than 10
  css px from its down, all lifted by `up` within 250 ms of the first
  down; a `cancel` of any contact voids the chord in progress (and a
  `pointercancel` is never a lift, so a pinch, which cancels both touches,
  never undoes); a fourth contact or a late one (after 80 ms) voids it; a
  chord while a `pen` contact is down (seen by `down` and not yet lifted
  or cancelled) is a palm and ignored. N = 2 fires `onChord('undo')`, N =
  3 `onChord('redo')`. In `'tap'` mode a chord fires as its last contact
  lifts; in `'double'` mode it fires when a second chord of the same N
  completes within 400 ms of the first's last lift with its centroid
  within 30 css px of the first's (`DOUBLE_MS` / `DOUBLE_SLOP`, the
  values `shared/ui/press.js` uses); `'off'` fires nothing but still
  tracks contacts, so `setMode` mid-gesture is safe. `reset()` forgets
  everything (window blur).
- `ink/stripKeys.js` (pure): `stripKey(event, state) → action | null`, the
  strip's key table (matched by `e.code`), dispatched by App's existing
  capture-phase handler, for the keydown and, for `E` alone, the keyup.
  `state` is `{tools: the visible presets, tool, hasSelection, boxUndo,
  hasBox, optionsOpen, eraserDown}` (the last the timeStamp of the `E`
  keydown still held, or null, so the keyup's `held` is pure arithmetic).
  A chord with Ctrl, Cmd or
  Alt is not the strip's, except Ctrl+Z / Y and the AltGr pair (`ctrlKey
  && altKey` together, a third-level letter on some layouts, treated as
  no modifier). Actions: `{kind: "pick", id}` for `1`–`9` (the visible
  list), `P` / `H` (forward; with Shift backward, wrapping), `L`, `T`,
  `V`; `{kind: "width", step: ±1}` for `BracketLeft` / `BracketRight`
  (App steps the armed preset through `sizesFor(kind)` and says
  "{name} · {size} pt." on the pill, where `name` is the preset's colour
  and kind in words); `{kind: "eraser", phase: "down" | "up", held}` for
  `E` — `down` on a non-repeat keydown, `up` on the keyup with `held` the
  ms since the down, so App decides tap or hold ("Hold-E" under App);
  `{kind: "escape"}` resolved by App in order — `hasBox` leaves it to the
  box (null), `hasSelection` → `deselect`, `optionsOpen` → `closeOptions`,
  else `close`; `delete`; `undo`. Nothing with Mod-C / X / V / D / A: those
  are catalog commands.
- `ink/pencilActions.js` (pure): `pencilAction(message,
  prefs, state)`, the Pencil double-tap and squeeze mapping App applies, and
  `backTool(prefs, state)`, the tool to go back to (the one armed before,
  when it is still on the strip and not the current one, else the stylus
  pen), which the Pencil's switchPrevious and App's `inkBack` both read.
- The replay (`ink.js`, pure;
  [recording.md](recording.md) "One clock for every replay"):
  - `strokeWalls(stroke)` is each sample's wall-clock ms (`t0` plus its
    `t`), null for a stroke without timing; `inkWallSpan(ink)` the
    stretch a drawing's timed strokes cover.
  - `inkTimeline(ink)` puts the strokes in the order they were written.
    A stroke starts at `t0` plus its first sample's `t`, and the file's
    order breaks ties. A stroke with no timing counts as written right
    after the stroke before it in the file, its samples spread over
    300 ms. Each sample gets a replay time: gaps between strokes and
    inside one are cut to `REPLAY_PAUSE` (400 ms), and the whole replay
    is scaled down to `REPLAY_MAX` (15 s) when longer. Its `times` map
    (stroke id → the samples' replay times) is the compressed clock's
    table.
  - `inkUntil(ink, at, times)` is the drawing at an instant: the strokes
    begun by then in the order begun, the one being written cut short (a
    prefix of a stroke's delta-coded `pts` is its first samples, so that
    is a slice), the ids not yet reached; on a clock's table, or on the
    strokes' own wall clock without one. `inkAtTime(ink, timeline, t)` is
    `inkUntil` on the timeline.
  - `replay/playback.js` plays one replay at a time on animation frames,
    on a clock (`replay/clock.js compressedClock` for a group's or a
    sheet's own replay). `replay/useFrame.js useGroupFrame(groups,
    surface)` gives a layer or a card the state only while one of its own
    groups or its sheet takes part, so only they re-render; `InkLayer.jsx
    ReplayStrokes` draws the group as it stood. `InkReplayButton` starts
    and stops it. An edit to the group (`applyInk`) or leaving the page
    ends it.
- `ink/inkStore.js`: files by URL, and per-block **drafts** — the strokes as
  edited here, ahead of upload. A draft wins over the block's file until
  the upload replaces `ink_url` with the draft's; a remote `ink_url` change
  on a block with nothing unsaved drops the draft. `loadInk` runs
  `upgradeV1` and `withDefaults` on what it fetches, so a version 1 file
  (a copy that has not run the upgrade step) renders and, once edited,
  uploads as version 2. A draft keeps its base's stroke ids (`baseIds`),
  which the save sends as `base_ids`.
- `ink/InkLayer.jsx`: `InkLayer` (one per surface: a PDF page, a sheet in
  the notebook view, a sheet among the notes; it draws after the
  highlights and the text boxes) is the retained SVG plus a
  `desynchronized` canvas for the stroke in progress. The SVG draws every
  group's band-0 strokes first, then every group's band-1 strokes (two
  `<g data-ink-id>` per group at most); `Strokes` alone, as in the notes
  card, orders one drawing by band. A drawing `inkFeatures` finds
  something in is reported once (`onInkFeatures`), and App puts one pill
  per page visit on it: "Some handwriting here comes from a newer Gamma
  and is drawn plainly; update to see it as drawn". `InkSelectionMenu`
  is a portalled `ContextMenu` (its controls sit outside the page's pointer
  listeners); `useSelectionMenuAnchor` gives it the selection's edges and
  the band it stays in, and the menu, from its own measured height, picks
  above or below before it is painted ([ui-design.md](ui-design.md#menus-and-popups-open-in-place)).
  Its first row is fixed — Color, Width, Style (`disabled`, title "Pen
  styles arrive in a later release"), Copy, Duplicate, More (`ctlBtn`,
  `MoreIcon`, aria-label "More", title "More edits", `aria-expanded`),
  Delete — and More shows the `.inkEditOptions.inkEditMore` row (its
  `aria-label` "More edits"): Cut, Flip horizontal (`FlipHIcon`), Flip
  vertical (`FlipVIcon`), New note (`NotebookPenIcon`), Merge into note
  (`MergeIcon`, rendered only when `canMerge`: the selected ids are
  exactly one group's strokes and the surface has another group, which
  the layer works out from `groups`), Select note, Show note; whether it
  is open is a module-level flag, so it lasts the session. The Color and
  Width rows render under the open rows, one at a time. Copy, Cut and
  Duplicate take their title's chord from `ink.keys` on the context
  (`"Copy · Ctrl+C"`; the label alone when unbound). `usePalette` is
  called before the early return, so the hooks' order never changes. The
  buttons dispatch `onAction(page, kind, arg)` with `copy`, `cut`, `flip`
  `{axis: 'h' | 'v'}`, `split`, `merge` beside the older kinds.
  A text box's menu is placed the same way. `InkTransformHandles` are the resize/rotate
  buttons; `InkTooltips` shows a button's title on hover or focus for the
  strip and the menus, since native titles are unreliable under Pencil
  hover. A press takes the tooltip away and keeps it away while the pointer
  stays on that button, and a button with `aria-expanded="true"` gets none,
  so no tooltip flickers over the menu the press just opened. A menu such a
  button opens belongs outside the `InkTooltips` host: React bubbles a
  portal's events to the host, so every row the pointer crossed would raise
  a tooltip. It opens on the press, not the click, so the menu takes the
  tooltip's place in the same frame (the recording player's `PlayerBar`).
  A tip goes below its button unless that is near the window's bottom;
  `above` keeps a strip's tips above, on the side its menus open.
  `InkCard` is the picture in the notes. For `markup/` the file
  exports `InkTooltips`, `inkColorName`, `ERASER_SIZES`, `ColorChoices` (a
  palette role's swatches plus a custom colour, for a pen preset's row and a
  text box's colours; the strip, the selection menu and the text box read
  the account's palette through `usePalette`, `shared/ui/paletteContext.js`)
  and `swallowClick` (eats the click a handled
  pointer-up would deliver). Imports run from `markup/` to `ink/`, never
  back.
  - Claiming input: a capture-phase `pointerdown` listener on the page
    wrapper takes the pointer when a tool is armed or a stylus touches the
    page (`pointerType === "pen"` with *Stylus draws right away*), so text
    selection and the area drag never see it. Other pointers pass through.
    Pointer-up encodes the stroke and swallows the click it would deliver to
    whatever lies beneath. Lost capture, `pointercancel` and window blur
    discard the unfinished stroke.
  - Touch: non-passive capture `touchstart`/`touchmove` listeners cancel the
    Pencil's touch gesture on iPad Safari (cancelling the pointer events
    alone does not stop native panning). They match stylus touches, or any
    touch while a pen pointer is down, and leave finger scrolling and pinch
    zoom alone between strokes. The global `html { touch-action:
    manipulation }` (app.css) removes Chrome's double-tap zoom while keeping
    panning and pinch zoom.
  - Palms: while a pen is down, finger touches on that page are swallowed
    and kept from the viewer's pan/pinch handlers. A pen takes over an
    unfinished finger stroke when the palm landed first; a second contact
    never takes over a pen.
  - A second finger makes a finger's stroke a pinch: the stroke in
    progress is discarded.
  - A finger moving alone over a page with a tool armed and *Pen only* on
    calls `onFingerScroll` (one of the page tools' actions), once a
    session (`fingerHint`, which a pen press also spends); App puts the
    hint on the status pill ([ui-design.md](ui-design.md#the-status-pill)).
  - Samples (`ink/inkInput.js`): `getCoalescedEvents()` where available
    (Safari since 18.2; older Safari delivers 120/240 Hz moves without it), hardware timestamps,
    the pressure preference snapshotted at stroke start, the pointer-up
    position with the last contact pressure (up reports zero), repeated
    points dropped. The live outline gets the same endpoint treatment as
    saved ink, so it reaches the pen tip. With `getPredictedEvents()` the pen
    preview adds at most 16 ms / 12 CSS px of prediction, expiring after
    32 ms and never encoded.
  - Canvas: `shared/lib/canvasSize.js` caps the live bitmap (8 Mi pixels /
    4096 per edge) and the context transform uses the real backing-to-page
    ratio; lift and cancel release the bitmap. Canvas and SVG share the
    dark-page colour filter, and so does a text box's body.
  - Selection: the lasso draws its polygon on the same canvas. A drag inside
    the selection box moves the selected strokes (previewed as a translated
    copy, committed on pointer-up). A pending tap/hold state, separate from
    the drawing, lets a native scroll cancel a touch selection without ink.
  - Smoothing: the draw context snapshots the preset's `stabilize` at the
    down (as it does `pressure`) and holds a `makeStabilizer(level, {k})`;
    the layer's `takeSample` pushes each pointer sample (`inkSample`)
    through it and keeps what comes back (a preset without smoothing goes
    through `appendInkSample` as before), the lift appends `finish`'s
    catch-up, and prediction is off (the raw predicted positions would
    spike from the brush to the pointer). The live canvas draws the
    stabilised samples, so the line under the pen is the committed line;
    at level 4 it also draws a faint 1 px string from the brush to the
    pointer. A hold recognises the samples plus the tip, so a held line
    ends where the pen rests, not a string's length short of it. The iPad `claim.commit` feeds the host's raw
    samples through the same stabilizer before `finishStroke`.
  - Draw and hold: with `ink.drawHold` on (and, for a highlighter, the
    preset's `straight` not `'off'`), a hold timer on the draw context
    restarts whenever the tip moves more than 3 css px and fires after 600
    ms still when the stroke's chord is at least `max(24 css px, 18 pt)`
    and it lasted at least 250 ms before the tip went still. On firing,
    `recognise(d.samples, {k, kinds})` (`['line']` for a highlighter)
    sets `d.hold = {kind, params, from: the tip, at: now}` on a hit, the
    canvas draws `shapeSamples` of it instead of the trace (two frames in
    the `inkSelRect` blue, then the preset's look), and once the tip has
    moved 6 css px every further move refits through `refitShape(kind,
    snapParams, {pointer, from, shift: e.shiftKey})`; a move of more than 12 css px
    within 150 ms of the snap clears `d.hold` and the freehand trace
    resumes. Shift with a mouse (`pointerType === "mouse"`) recognises at
    once. The timer is cleared in `handOver`, `finish` and `cancel`;
    `claim.commit` never reads a hold (the shell sends samples at the
    lift only). `commitStroke` passes `hold: d.hold` to `finishStroke`
    and the result's `status` to `onHoldStatus`.
  - Paste here: with `ink.clip` (the clipboard holds strokes) and a pen
    or highlighter preset armed, a pen or mouse down in stroke mode arms
    the draw-and-hold timer with the chord floor at 0; when it fires
    before the tip has moved 2 css px (600 ms still), the draft is
    cancelled — nothing is committed — and a one-row `ContextMenu` (a
    `MenuItem` *Paste here*, `ClipboardIcon`) opens at the point; its row
    calls `onPaste(page, pt, {width, height})` with the point in page
    points and the surface's size. The draft's `pasteArmed` flag and its
    `travel` (the farthest the tip went from the down, css px) are what
    `holdFire` reads, before the shape check; the lift that follows is
    not a click (`pasteHold`). A finger
    never starts it: the flag checks `pointerType`, so a resting palm
    cannot paste. The wrapper's `contextmenu` listener opens the same row
    for a mouse right-click when a tool is armed, no stroke is under the
    pointer and the clipboard holds strokes, and `preventDefault`s only
    then; otherwise the page's own menus run. Chord 0 against the hold's
    `max(24 css px, 18 pt)` keeps the two timers apart: a tip that has
    travelled cannot paste, one that has not cannot snap.
  - Erasing: the draw context records `eraseSource: 'tool' | 'barrel'`
    (the armed eraser, or the pen's eraser end / barrel button). `finish`
    in erase mode calls `onEraseEnd()` once per gesture for `'tool'`
    only, whether or not anything was erased; a gesture ended by
    `pointercancel` does not. The whole-stroke pass hands its candidates
    to `onErase(page, gid, ids)` and App applies the eraser filter there;
    the hover ring draws a small pen or highlighter glyph when
    `ink.eraserFilter` is not `'all'` (the `.inkCursor`'s `data-filter`
    shows the matching `.inkCursorGlyph` in the centre mark's place).
  - Finger chords: `wireChords` installs one module-level
    `makeChordDetector` (the `hostClaims` pattern: a singleton, so several
    page layers never double-fire) behind its own four document capture
    listeners (`pointerdown`/`move`/`up`/`cancel`) and a window `blur`
    that resets it. Each layer with an `onUndoGesture` registers its wrap
    element in `chordHosts`; the first touch of a chord that lands on a
    layer makes it the host whose `fingerUndo` sets the mode (`setMode`)
    and whose `onUndoGesture(kind)` the chord fires (a touch off every
    layer changes neither), and a touch while the host's `penBusy()` says
    a pen is writing — the page's own pen stroke or one the native host
    took, whose pointer WebKit has cancelled — is a palm (`palm()`). The
    wiring goes with the last host's cleanup, so chords exist exactly
    where handwriting can be drawn. The detector forgets a contact whose
    lift never came after 1 s, so a dropped pointerup never pins every
    later chord. In
    finger-draw mode the chord's first finger starts a stroke and the
    second cancels it as a pinch; that stroke counts toward the chord, and
    the stroke start's `onSelect(page, [])` is deferred until the stroke
    has moved 8 css px, as the pending path defers a tap, so a chord never
    clears the selection it is about to restore.
- `markup/` ([text_boxes.md](text_boxes.md) "Client"):
  - `MarkupToolbar.jsx` `MarkupToolbar` is the strip. Its first row
    (`.pdfInkRow`) is five groups split by `.pdfInkSep`: the presets; the
    eraser, the lasso and the Text tool; the hand; **More** and close; and
    the history buttons last, whose labels follow its `blockHistory`
    flag. Its menus share one hoisted `menu` state `{kind, x, y, id?}`
    (`'more'`, or `'tool'` with the preset's id) rendered as a
    `ContextMenu` **after** the `InkTooltips` element and opened on the
    press (`onPointerDown` for button 0, `onClick` only for a keyboard's
    `e.detail === 0`: the recording player's `menuButton`), never through
    `MenuButton` or `ActionMenu`, which open on the click inside the host.
    The More button (`ctlBtn`, `MoreIcon`, aria-label and title "More
    handwriting tools", `aria-haspopup="menu"`, `aria-expanded`,
    `data-guide="ink.more"`) lists `CommandMenuItem`s for `ink.paste`
    (titled "Copy or cut handwriting first" while greyed) and
    `ink.selectAll`, a `MenuDivider` and *Handwriting settings…*
    (`SettingsIcon`, `onOpenSettings("reading", "Handwriting")`, which
    App serves through the `handwriting` settings alias
    (`settings/settingsNavigation.js`): the Reading & editing pane,
    scrolled to the section's first row, *Reset presets*). A
    preset's ⋯ (`ctlBtn`, `MoreIcon`, aria-label "Tool menu", title
    "Move, hide, reset or remove this tool", `aria-haspopup`,
    `aria-expanded`) lists Move left, Move right (each `disabled` at its
    end of the visible row), Duplicate, Hide (`EyeOffIcon`), Hidden tools
    (a `SubMenuItem` of the `hidden` presets by their names; `disabled`,
    title "No hidden tools", with none), Remove (`danger`), a divider and
    Reset this tool, each through `onChangeTools(list)` with the ids
    kept. The options rows (`.pdfInkSub`): a preset's holds the three
    captioned sub-rows (`.inkOptRow` with an `.inkOptCaption`), Style —
    the Pen · Ballpoint pills through the `seg` helper (a highlighter's
    *Straight* trio in their place) and the *Smoothing* five-dot ladder,
    `inkSizeBtn` dots for `stabilize` 0–4 —, Color — the `ColorChoices` —
    and Width — the eight dots, Duplicate, Remove, ⋯; the eraser's row,
    after the sizes, the *Erases* trio and the *Back to last tool*
    toggle, `aria-pressed`, title "After erasing, return to the tool you
    had". Below 480 px the captions go and Style and Width wrap onto one
    line. A preset button is `touch-action: none` and carries the drag
    (`usePresetDrag`: a mouse past 6 px, or a touch or pen past `HOLD_MS`
    500 ms from `shared/ui/press.js`, takes `setPointerCapture`, draws
    the `.inkDragGhost` and the `.inkDropBar`, says "Drop to reorder." and
    on release calls `onChangeTools` with the moved list and
    `swallowClick`s the click; Escape or a release outside the strip
    cancels; a hold that never moved is the button's ordinary click).
    Hidden presets are filtered out (`visibleTools`) before the row and
    the key lists see them.
  - `MarkupLayers.jsx` `<MarkupLayers>` is the one place a surface's layers
    mount (`PdfPage`, the notebook view's sheet, `NoteSheet`). It maps the
    tools and the surface's marks onto `InkLayer`'s props. A read-only
    surface gets no edit callbacks, and one with no ink mounts no layer.
    While the Text tool is armed the layer only draws.
  - `useMarks` (same file) builds App's marks per surface: its ink groups,
    and the lasso selection and the flash when they are on it. A surface's
    slice is kept while its parts are unchanged, so a memoized page
    re-renders only for its own ink.
  - `PageTools.jsx` `PageToolsContext` carries the tools to every layer:
    `{readOnly, ink: {tool, penTool, penOnly, pressure, eraserMode,
    eraserSize, eraserReturn, eraserFilter, drawHold, scribble,
    fingerUndo, lassoMode}, text, actions}` — `eraserReturn`, `drawHold`
    and `scribble` the account flags, `eraserFilter` `'all' | 'pen' |
    'highlighter'`, `fingerUndo` `'off' | 'tap' | 'double'`. The actions
    are the stroke, erase, select, action, move, jump and finger-scroll
    handlers (and the text boxes') plus `onEraseEnd()` (the tool eraser
    lifted), `onEraseMany(page, [{gid, ids}])` (a scribble's targets, one
    entry across groups), `onUndoGesture('undo' | 'redo')`,
    `onHoldStatus({text, once})` and `onPaste(page, pt, {width, height})`
    (a held pen or mouse, or a right-click, asking for the clipboard at a
    page point, with the surface's size in points),
    ref-backed through `useStableActions`, so their identity never changes.
    `ink` also carries `clip` (the clipboard holds strokes) and `keys`
    (`{copy, cut, duplicate}`: the chord labels the selection menu puts
    in its titles).
    `armedClasses(tools)` gives a surface's container its armed-tool
    classes (`inkArmed`, `inkTouchDraw`, `textArmed`).
- `app/App.jsx` owns the tool state: `inkUi` (`open`, the armed `tool` — a
  preset id, `eraser`, `select`, `text` (the Text tool) or `null` for the
  hand — its `options` row,
  and `pen`, the last pen preset, which a stylus writes with when nothing
  is armed — resolved among the visible presets, so a hidden pen falls
  back to the first visible one) plus the prefs (`inkTools`, the
  account's preset list validated by `ink/ink.js` `normalizeTools` —
  App's `setInkTools` runs every write through it, the updater form the
  pill's *Show* passes included, so the stored and pushed list is the
  canonical one — which keeps a preset's
  `stabilize` 0–4 as an integer, omitted at 0, a highlighter's `straight`
  `'always' | 'off'`, omitted at `'hold'` and dropped from a pen, a
  `hidden: true` flag, omitted when false, and any other key a preset
  carries, verbatim, so an older build that edits one preset never strips
  what a newer one wrote — the known keys first and the rest only while
  the preset serialises under 512 bytes of UTF-8, and a list that hides every
  preset shows its first, so the row is never empty; `toolStyle` carries
  smoothing and straightening onto the armed tool for the layer, never
  onto a stroke, and `visibleTools(list)` is the list without the hidden
  ones, what the strip, the key table and the stylus's `backTool` read; the
  eraser's mode and size; the lasso mode; the account's `inkDrawHold`,
  `inkScribbleErase`, `inkEraserReturn` and `inkEraserFilter` and this
  browser's `inkFingerUndo`, [settings.md](settings.md) "Reading &
  editing"),
  the group the next stroke joins (`inkActiveRef`), the lasso
  selection (`inkSelection`) and the **stroke history** (`inkHistRef`:
  entries of `{changes: [{id, page, before, after}], label}`, one per action; a group whose
  block is gone is re-inserted when an entry brings strokes back, its old
  draft dropped first (`inkStore.dropDraft`): the block coming back is a
  fresh one with no file, so its upload must name no base — a base left
  from the deleted draft would make the server merge the strokes three
  ways against the new block's empty drawing and count them as deleted).
  Every
  edit funnels through `applyInk`, which checks the byte budget (an edit
  that would grow a group past `MAX_BYTES` is refused with "This group is
  full; split it or start a new one"), updates the drafts, records the
  entry and schedules `flushInk` (700 ms after the last edit, at most 5 s
  after the first unsaved one, and on `pagehide` — with `fetch`
  `keepalive`, the `PUT` beside the upload, see "Fences" — /
  `visibilitychange` / leaving the page). `handleInkStroke`
  starts a new group on the same surface when the current one would pass
  `GROUP_SPLIT_BYTES` (3 MB) or `MAX_STROKES` with the stroke.
  The flush uploads the draft (`POST /api/upload-ink`, the `serializeInk`
  bytes) and PATCHes the block through `PUT /api/blocks/{id}` with
  `inkProps` (the url, count and position), `base_properties: {ink_url}`,
  the file the draft was edited from, and `base_ids`, that file's stroke
  ids. That is a server-side writer, so the
  change fans out over the page socket and reaches this tree like a
  remote op. Only the group's block itself (first stroke) is inserted
  through the tree. The answer's `ink_conflict` goes on the pill
  (`reportInkConflict`, "Two writers, one group").
  - A draft remembers its base (`inkStore` drafts: `{ink, dirty, url,
    base, baseIds}`). When the answer names another file than the upload, the
    server merged someone else's strokes: `markSaved` loads the merged
    file and replaces the draft with it, with strokes drawn meanwhile
    merged on top.
  - The build fence: `noteServerBuild` keeps the names of the server's
    build as the session reported it (`/api/session` `build`: commit,
    label, version) and compares every socket hello's `build` with them;
    a build sharing none of the names sets `inkStale`, which disarms the
    tools (`inkTool`, `inkPenTool` null) behind the sticky pill "Gamma
    was updated, reload to keep drawing" with a Reload button. Taps,
    selection and reading go on.
  - An empty group with no caption and no notes is deleted the same way.
    Its empty draft keeps masking the saved strokes until the tree sees
    the deletion (the HTTP response can land before the socket op).
    `inkStore.markDeleted` marks it clean only after a successful delete,
    so a failure retries. An empty group with a caption or notes is saved
    as an empty drawing instead.
  - A failed flush retries after two seconds on a 5xx, a network error or
    a 404 (the block's insert may still be queued behind the tree's save);
    any other 4xx stops the retry and keeps the draft behind a sticky pill
    (`setStatus(msg, null, {sticky: true})`, "Two writers, one group",
    "Fences").
  - The eraser filter is one function: `ink.js` `eraserKeep(filter) →
    (stroke) => bool` (`'all'` keeps every stroke, `'pen'` tool `pen` of
    any brush, `'highlighter'` tool `highlighter`; a stroke of an unknown
    tool only under `'all'`). App applies it in `handleInkErase`,
    `handleInkErasePartial` and `handleInkEraseMany` — `hitStrokes(ink, x,
    y, radius, keep)` and `eraseAt(ink, x, y, radius, keep)` take it as an
    optional last argument and leave a stroke `keep` refuses untouched,
    the same object, so the caches survive — and the layer builds it from
    `ink.eraserFilter` for the scribble stage and the hover glyph.
  - `inkBack()` is the previous distinct tool, else the stylus pen
    (`pencilActions.js` `backTool` over `inkPrevRef`); `onEraseEnd` with
    `inkEraserReturn` on and the eraser armed picks it with the options
    row closed. The Pencil's squeeze toggles the eraser; with the return
    on, the lift re-arms the pen and the next squeeze arms the eraser
    again.
  - `handleInkEraseMany(page, targets)` deletes the targets across their
    groups as one entry, `T("ink scribble erase")`, and raises
    `undoableStatus(tn("Erased {n} stroke.", "Erased {n} strokes.", n),
    {ink: true})`. `handleHoldStatus({text, once})` raises
    `undoableStatus(text, {ink: true})`, a `once` status at most once a
    session (`straightHint`). The `ink` option makes the pill's Undo step
    the stroke history (`inkUndo`), as the strip's button does, where the
    plain pill runs the `app.undo` command, the block history. Turning
    `inkScribbleErase` on from Settings or the strip shows a pill
    explaining the gesture once.
  - `onUndoGesture(kind)` runs exactly what the strip's Undo / Redo
    buttons run (`undoInk` / `redoInk`, or `undoBlocks` under `boxUndo`);
    the first chord of a session also raises a pill, "Two fingers undid a
    stroke." / "Three fingers redid a stroke.", with a *Turn off* action
    that sets `inkFingerUndo` to `'off'` (`fingerUndoHint`, spent by the
    first chord).
  - **The handwriting clipboard** is `inkClipRef`, `{type: "gamma/ink",
    version: 2, space, box, strokes}`: the file's `space`, the selection's
    `inkBounds` box `{x1, y1, x2, y2}` in points and the raw stroke objects
    as the file holds them. Copy and cut fill it and, best effort, write a
    `ClipboardItem` beside it — `text/html` a `<div data-gamma-ink>` whose
    text is that JSON, `text/plain` the JSON — so another app or a later
    build can read it; a browser that refuses the write loses nothing,
    since the ref is the one source a paste reads (reading the system
    clipboard back is not built). The ref does not survive a reload.
  - `handleInkAction(page, kind, arg)` gained `copy`, `cut` (copy, then
    `deleteInkSelection` with the entry `T("ink cut")`), `flip` (`{axis}`
    → `flipStrokes` about the selection box's centre, `T("ink flip")`, the
    selection kept), `split` (`T("ink split")`: the strokes leave their
    groups and a new ink block on the same surface takes them, positioned
    by `pdfPositionOf`, one entry whose changes name the old groups and
    the new block, which `applyInk` inserts; undo removes it again) and
    `merge` (`T("ink merge")`: the selection's whole group appended to the
    page's previous group — `inkBySurface`'s order, the nearest before it,
    else the first after — through `appendStrokes` and emptied; `flushInk`
    deletes the empty block when it has no caption or children, and one
    that has them is refused before anything moves, with "Not merged: this
    note has a caption or notes under it."). The selection's strokes, box
    and space come from `selectedInk()`. Copy
    and cut raise `setStatus(tn("Copied {n} stroke.", "Copied {n}
    strokes.", n) | tn("Cut {n} stroke.", "Cut {n} strokes.", n), {label:
    t("Paste"), run: pasteAtCentre})`.
  - `handleInkStrokes(page, strokes, size)` is `handleInkStroke` for
    several strokes at once (`size` the surface in points, for a fresh
    group's file): they join the surface's current group when it can
    take them all under `MAX_STROKES` and `GROUP_SPLIT_BYTES`, else a new
    group takes them all (a paste is never split across groups), through
    `appendStrokes` as one entry, `T("ink paste")`; a set no group could
    hold is refused with "Too much handwriting to paste into one note."
    It returns `{id, ids}`, the group and the ids as they landed.
  - `pasteInk(page, at, size)` makes the copies with `cloneStrokes(strokes,
    dx, dy)`, `dx, dy` taking the clipboard box's top-left to `at` and then
    clamped so the box stays inside the surface (on a surface too small
    for it, its top-left corner stays on), hands them to
    `handleInkStrokes`, selects them and arms the lasso. `pasteAtCentre()`
    is it with the box centred on the surface in view (`pasteTarget()`):
    `pageInViewRef`, fed by `PdfViewer`'s `onPageInView(pageNumber,
    {width, height})` (one `IntersectionObserver` over the page boxes
    keeps the intersecting ones, and per scroll frame the one whose centre
    lies nearest the scroller's centre is reported when it changes, with
    its size at scale 1), or `nbCurrent` with its paper on a notebook. The
    commands, the pill's *Paste* and the More menu's row all run it; the
    page's `onPaste(page, pt, size)` (a held pen or mouse, a right-click)
    runs `pasteInk` at the point. `duplicateInkSelection()` (Ctrl+D) reads
    the page's scale off the selection rectangle's screen transform for
    the 12 px nudge; `selectAllInk()` takes the selection's surface, else
    the one in view.
  - The commands (`appCommands.js`, group `GROUP_HANDWRITING`
    "Handwriting", after Library in `GROUPS`; [hotkeys.md](hotkeys.md)
    "Widget keys"): `ink.copy` "Copy handwriting" Mod-c, `ink.cut` "Cut
    handwriting" Mod-x, `ink.duplicate` "Duplicate handwriting" Mod-d
    (the 12 screen-px nudge), when the lasso holds strokes
    (`c.inkSelected`); `ink.paste` "Paste handwriting" Mod-v, when
    `c.inkOpen && c.inkTool && c.inkClip` and
    `window.getSelection().isCollapsed`; `ink.selectAll` "Select all
    handwriting on this page" Mod-a, when `c.inkOpen && c.inkTool`, which
    selects every stroke of every group on the surface in view and arms
    the lasso. All five are `edits: true`; a `run` handed a key event
    from a text field returns `false`, so the browser keeps its own copy
    and paste there. `appCmdRef.current` carries `inkOpen`, `inkTool`,
    `inkSelected` and `inkClip` for the guards. Icons in
    `commandIcons.jsx`: `CopyIcon`, `ScissorsIcon`, `ClipboardIcon` (new
    in `Icons.jsx`, with `FlipHIcon` and `FlipVIcon`), `CopyIcon`,
    `RectSelectIcon`.
  - **Hold-E**: the strip handler takes `stripKey`'s `eraser` action —
    `down` arms the eraser as a tap does and keeps `inkHoldRef = {since,
    erased: false, selection, tool}` (the lasso selection and the tool
    armed before); `onEraseEnd` marks `erased`; `up` with `held ≥ 300`
    and `erased` picks `tool` again and, when that is the lasso, restores
    the snapshot; a shorter press, or one that erased nothing, leaves the
    eraser armed; a window `blur` clears the hold.
  - **Presets on the account**: `useProfileSync` reports, on the
    account's first pull only, an account entry whose server value
    replaced a local one that was neither the default nor equal to it
    (`onReplaced({name, local})`; a later pull that differs is another
    device's change and applies silently); for `inkTools` App raises
    "Use this browser's pens instead?" with *Use this browser's*, whose
    action sets `inkTools` to the local list (and so pushes it), once a
    session.
  - The strip's pills: *Move left* / *Move right* and a drop say "Blue
    pen is now key 3." (or "Blue pen moved." past the ninth place), *Hide*
    says "Blue pen hidden." with *Show*, `[` / `]` say "Blue pen · 2.8
    pt." — the name is the preset's colour and kind in words, as the
    button's label spells them (`inkToolName(preset)`, which
    `MarkupToolbar.jsx` exports beside `moveTool(tools, id, slot)`, the
    reorder a drop and Move left / right share).
- With the strip open, Ctrl+Z is the stroke history (a capture-phase key
  handler, so the page's block undo never sees it); with it closed, Ctrl+Z
  is the page's block history, which knows the group's block but not its
  strokes. Text boxes are blocks: while the Text tool is armed or a box is
  selected or edited (App's `boxUndo`), the handler leaves Ctrl+Z to the
  block history, and the strip's buttons step it (`undoBlocks`). Escape
  goes to a selected or edited box. An undo or redo applies its entry onto the
  group as it is now, by stroke id (`mergeInk`), so strokes someone else
  drew meanwhile stay.
  Two clients drawing into one group keep both drawings (see "Two
  writers, one group").
  A focused note editor keeps the block history even with the strip open,
  and other text inputs keep their own undo; an empty ink history never
  falls through to block undo. Undo and redo report the action and PDF page
  in the status pill (“Undone: ink width change (page 1).”); the labels
  distinguish drawing, erasure, partial erasure, move, colour, width,
  duplicate, delete, paste, cut, flip, split and merge.

## Server

- `gamma/ink.py`: the pydantic schema (`extra="allow"` with the caps) and
  limits, the codec, `upgrade_v1` (the version 1 converter), `read_upload`
  (the parsed file behind a block's `ink_url`, None when unreadable — the
  exporters' one loader),
  `stroke_width` (`size × w`, else `size`: no law), `stroke_polyline`
  (variable-width polylines every renderer draws from, band 0 first),
  `bounding_box` / `pdf_position` (the shape is `gamma/highlights.py`'s),
  `to_svg`, `pdf_path_ops` (content-stream operators for the notes-as-PDF
  writer), `ink_buckets` and `from_pdf_ink`
  for the `/Ink` interchange, `dumps` (the canonical bytes, equal to the
  client's `serializeInk`, so the same strokes dedup to one upload),
  `merge_ink` (→ the file, `clean`, `lost_ours`, `returned`).
- `POST /api/upload-ink` (`routers/ink.py`): the file as the JSON body,
  validated, stored as it came as `<hash of the bytes>.ink` with the usual
  quota check → `{url, size, strokes, bbox, pdf_position, already_existed}`;
  a version 1 file is refused with `400 "update Gamma"`.
  `POST /api/upload-file`, the route a mirror pushes files by, stores an
  `.ink` only when it is a valid version 2 drawing. Editors and
  edit shares (`require_ws_writer`). `.ink` is in `storage.FILE_MEDIA_TYPES`
  (`application/json`), so `GET /api/uploads/<hash>.ink` is the ordinary
  upload route with its share scoping and cache headers.
- Interchange ([import_export.md](import_export.md),
  [pdf_typesetting.md](pdf_typesetting.md)): the annotated PDF
  writes one `/Ink` per look bucket (colour × tool × size × opacity;
  every group's band-0 buckets before any band-1 one in a page's
  `/Annots` — `_add_ink_annotations` sorts a page's annotations by band,
  stably, since `/Ink` has no blend mode — the note carrier and the
  Zotero `/NM` keeping the first-seen bucket index) with
  `/InkList` in user space, the mean drawn width as `/BS /W` (from `w`),
  the note on the first, an `/NM` Zotero needs to import it as ink (the
  Zotero export's PDF copies carry it too), and a private `/GammaInk` key
  carrying the bucket's v2 strokes verbatim so a Gamma re-import keeps
  pressure, time and every key; the embedded-annotation importer reads
  `/Ink` (Gamma's or anyone's) into ink blocks, converting a version 1
  `/GammaInk` payload, and strips them like the other types; the Markdown export writes
  `![Handwriting (p.N)](assets/<hash>.svg)` with the SVG generated into the
  zip; the notes-as-PDF document draws the strokes as vectors under a
  "handwriting, p. N" line. The agent's `read_block` outline labels an ink
  block "handwriting on p. N, K strokes" before its caption.
- `gamma/ink_view.py`: the picture the chat sees of a group (`view_ink`,
  and an attached handwriting block), drawn where it was written. A sheet
  goes through `notebook_pdf`, a PDF page through
  `pdf_export.page_with_ink` (that page alone with the groups as `/Ink`),
  and MuPDF rasterizes either ([ai_tools.md](ai_tools.md) "view_ink").

## Not built yet

Dedicated shape tools (a shape comes only from draw-and-hold; a committed
shape has no handles yet, though its `shape` record is written for them),
shape recognition and hold-to-paste on the iPad app (the shell sends
samples at the lift only, so no hold runs there), pen styles beyond Pen
and Ballpoint (the selection menu's Style button waits for the brush
table, greyed "Pen styles arrive in a later release"), dashed and dotted
strokes, reading handwriting back from the system clipboard (copy writes
a `gamma/ink` item for other apps, but a paste reads only this tab's own
clipboard, so nothing pastes across tabs yet), Xournal++ `.xopp` import,
live co-drawing over presence, and a
replay of erasures and edits (it shows the drawing as it is, in the order
it was written). Audio recording to go with the replay is
[recording.md](recording.md). Obsidian vault export writes an ink block's
caption only. The Notability comparison in the research note lists what a
closer pen experience still needs (a snap of
straight highlights to the PDF's text lines); draw-and-hold, smoothing,
the straight highlighter, scribble-to-erase, the eraser's return and
filter, finger undo, the highlighter
drawn behind the ink (`band`), the handwriting clipboard, flips, note
split and merge, presets on the account and their drag order are built.
Live co-drawing is pinned as a
presence message carrying raw samples and the style, drawn by peers
through the same brush table, replaced by the committed stroke of the same
id when its file lands. The broader interaction survey is
[handwriting-interactions.md](../research/handwriting-interactions.md).
