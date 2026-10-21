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
`ink/pencilActions.js`, `ink/hostInk.js`, `ink/inkStore.js`, `ink/inkJournal.js`,
`ink/inkFlush.js`, `ink/inkLive.js`, `ink/grain.js`,
`ink/InkStylePicker.jsx`, `ink/InkLayer.jsx`, `ink/inkInput.js` (client),
with the
tool strip and the page plumbing it shares with text boxes in
`markup/MarkupToolbar.jsx`, `markup/MarkupLayers.jsx` and
`markup/PageTools.jsx`; tests `backend/tests/test_ink.py`,
`frontend/tests/ink.test.mjs`, `inkErase.test.mjs`, `finishStroke.test.mjs`,
`shapes.test.mjs`, `gestures.test.mjs`, `stabilizer.test.mjs`,
`fingerChords.test.mjs`, `stripKeys.test.mjs`, `pencilActions.test.mjs`,
`hostInk.test.mjs`, `inkInput.test.mjs`, `inkLive.test.mjs`,
`inkJournal.test.mjs`, `inkFlush.test.mjs` (and the benchmarks
`tests/bench/inkCache.bench.mjs` and `inkErase.bench.mjs`, not part of
`npm test`), the
shared fixtures `tests/shared/inkv2.json`, `inkwidth.json`, `inkdash.json`
and `inkmerge.json` (read by `backend/tests/test_shared_fixtures.py` and the
node tests, `inkwidth.json` by the iPad app's `InkWidthTests` too) and
`inkshapes.json`, `inkscribble.json` (the recognisers run in
the node tests only, the server never recognises; the Python side checks
the fixtures' shape and that every stored `shape` record parses) and
`inkhold.json` (the hold's stillness and refit rule, run by the node tests
and the iPad app's XCTest), e2e `tests/e2e/scenarios/ink.mjs`
and `inkEditing.mjs` (the draft journal's restore in `ink.mjs` too), and the live stroke in `collab.mjs`. The same files are
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
  width). **Style**: for a pen, the **Style picker** — a button showing
  a short stroke drawn in the current style, its name and a ▾ (*Pen ▾*),
  which opens a menu on the press, like More, with four rows, each led by
  the same stroke preview: *Pen* (width follows the stylus pressure),
  *Ballpoint* (an even line at every pressure; the stored brush is still
  `monoline`, a word that never shows), *Brush* (a felt tip: twice as
  wide at full pressure, thin at none, tapering in at the start and out
  at the end; with a mouse or finger it swells where the pointer moves
  slowly and thins where it moves fast, so it is a felt look without a
  stylus) and *Pencil* (graphite: the line darkens with pressure and
  widens as the pencil leans, with a grain on the screen; a mouse draws a
  soft even line). The current row is ticked. The preset reads "Red
  ballpoint pen · 4 pt · key 1", "Blue brush pen · 2 pt", "Black pencil ·
  2 pt". `Shift+[` / `Shift+]` step the armed pen through the four, in
  that order. Then the **Line** trio, *Solid* · *Dashed* · *Dotted*, for
  pens and highlighters alike (dashes are two widths long with two-width
  gaps; dots sit two widths apart; the pattern follows the width, so a
  thicker pen has longer dashes), then the *Smoothing* ladder of five
  dots (Off, 1–4: the preset's `stabilize`, "Smoothing" below); on a
  highlighter the *Straight* pills (Hold · Always · Off, its `straight`,
  "Straight highlighter" below) stand where the Style picker would.
  Calligraphy is a later release.
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
  the pill repeats the width), `Shift+[` / `Shift+]` step the armed pen's
  style back / forward through Pen, Ballpoint, Brush, Pencil, wrapping
  (the same pill, now naming the style: "Blue brush pen · 2.8 pt."; a
  highlighter has no styles and the keys do nothing), `L` `T` `V` the
  lasso / Text tool / hand,
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
  a gesture ended by `pointercancel` does not return). The partial
  eraser cuts exactly at its edge along the whole path it swept, however
  fast it moves: a stroke it crossed between two of the positions the
  pointer reported, or between two of the stroke's own samples (a fast
  mouse stroke, a held straight line of two samples), is cut there too,
  each piece ending where the eraser's edge met the ink, and a scrap
  shorter than half the line's width goes with the rest. A pass, from
  the press to the lift, is one undo entry even across groups, whichever
  mode erased. The
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
  (`pen: false`, no pressure) in the preset's colour, width, style and
  line, at that width end to end whatever the style: a shape is constant
  width, so a Brush preset's has no tapers and a Pencil preset's no
  shading (its grain stays), and the snapped shape under the pen is drawn
  the same way. Its `t` is spread over the time the stroke took, and it
  carries a `shape` record ("The stroke file"); the pill says
  "Straightened." or "Shape: circle." with
  Undo one tap away, every time. In the iPad app the shell owns the pen,
  so it tells the page when the Pencil has held still (`hold`,
  [ipad.md](ipad.md) "The pen"): the page recognises the shape then, the
  shell draws it under the pen and refits it by the same rules, and the
  lift commits it as here. Under a shell that does not send `hold`
  nothing is recognised: a shape is never recognised at commit time
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
  changes: Color, Width, Style, Copy, Duplicate, More and Delete.
  **Style** opens its own row: the Style picker (the strip's, shown when
  the selection holds pen strokes; its button names the style they share,
  or *Mixed*) and the Line trio; a pill is pressed only when every stroke
  it applies to matches (the brush looks at pens only, the line at every
  stroke). Choosing a style re-bakes the selected pens' widths from the
  pressure and tilt they were written with — a pen becomes a brush pen
  with its tapers, a pencil with its shading — while a snapped shape
  takes the style's name and stays constant width, and highlighters are
  left alone; choosing a line applies to everything selected. Each choice is
  one undo entry, "ink style change" or "ink line change". A restyle that
  would push the group past its byte budget is refused with the usual
  pill.
  **More** (⋯, `aria-expanded`) adds the second row, "More edits": Cut,
  Flip horizontal, Flip vertical, New note, Merge into note (only when
  the selection is one whole group and the page has another), Select note
  (every stroke of the selected ink blocks) and Show note (jump to the
  notes pane) — at most seven buttons in a row, a hard cap for every later
  addition. Whether the second row is open is remembered for the session.
  Color, Width and Style open their option row under whichever rows show,
  one at a time. Edits apply at once and keep pressure and time; a mixed
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
  drawn 2 css px, opens a one-row menu, *Paste here*, at the point (in
  the iPad app through the shell's `hold`, so not under an older shell);
  a **right-click** on blank page with a tool armed opens the same row.
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
  around a centre mark. With *Hover preview* on (this device's
  `inkHoverPreview`, on by default) the footprint is the armed preset's
  look: a round dot for a pen, a ballpoint or a highlighter, a teardrop
  for a brush pen, for a pencil an ellipse that leans the way the pencil
  does when the pointer reports tilt (a round dot otherwise), and the
  ring dashed or dotted when the preset's line is. Off, every pen and
  highlighter shows a plain round ring at its width. Pen and highlighter
  widths follow the zoom; the eraser radius stays in screen pixels, like
  the erasure. Tool and menu buttons show their description on hover or
  keyboard focus.
- **The pencil** draws graphite: the line is filled with a fine grain,
  darker where the pencil pressed harder (three shades on the page) and
  wider where it leaned; under the pen the live line is stamped from
  small round dabs of the same grain, as wide as the saved line. Exports
  and the AI's picture draw the pencil as a plain line in the same
  shades, without the grain.
- **Live co-drawing.** While someone else draws on a page this tab has
  open, their stroke appears as it is written, in their preset's look;
  when their drawing is saved the wet stroke becomes the saved one, the
  same line — except a stroke that snapped (a held shape, a straightened
  highlight), whose freehand trace gives way to the shape. A stroke they
  undo or erase before it is saved goes at once; one whose save never
  arrives goes a minute after its pen lifted, and every wet stroke of
  someone who leaves goes with them. A stroke written in the iPad app
  shows once it is saved. There is nothing to switch on.
- **Dense pages** stay fluid: a group of more than 200 strokes appears
  first as thin lines that fill out to their true widths within a moment;
  groups scrolled far off the visible part of a page are not drawn; on a
  page holding more than 1 500 strokes each finished group is shown as a
  picture (as many as fit in about 64 MB of memory a page, the groups in
  view first), redrawn when it is edited or the zoom changes, so writing
  stays quick. None of this changes what is saved.
- **Unsaved handwriting comes back.** What was drawn but not yet saved
  when a tab closed (a crash, a lost connection) is kept in this
  browser for a week; on the next visit it is restored into the page and
  saved, and the pill says "Unsaved handwriting from a previous visit was
  restored." with *Discard* beside it, which takes it off again (an
  undoable step). Strokes someone deleted meanwhile are not brought back
  — not even those the closing tab's last save had carried to the server
  without hearing back, nor those it had only restyled — and what a tab
  still open elsewhere has not saved yet is left to that tab.
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
- **Settings** for handwriting sit under Reading & editing
  ([settings.md](settings.md) "Reading & editing"):
  **Handwriting**, the account's — *Reset presets* (with a confirm),
  *Eraser returns to the last tool*, *Erases*, *Draw and hold*, *Scribble
  to erase*; the presets themselves (`inkTools`) are the account's too,
  though they are edited on the strip — **This device**, this
  browser's — *Draws with*, *Stylus draws right away*,
  *Pressure-sensitive strokes*, *Undo with fingers*, *Hover preview* —
  and, in the iPad app only, **Apple Pencil**, this browser's too:
  *Double tap* and *Squeeze*, what the Pencil's gestures do
  ([ipad.md](ipad.md) "The pen"), and *Haptics*. Presets, smoothing
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
multiplied onto the page and drawn under the ink); three brushes of the
pen, `monoline` (Ballpoint, a pen of constant width), `brush` and
`pencil`, the absent brush being the plain Pen. Both are open strings in
the file, so a later pen style is a new value and a renderer that does
not know it draws the stroke plainly from its baked width (below,
"Width"). Pressure, timing and tilt stay in the samples, raw; the width a
pressure brush drew with is baked into the samples once, when the stroke
is finished, as a multiplier of the stroke's nominal size, and the
pencil's shading the same way as an opacity multiplier. Live canvas,
retained SVG, eraser geometry, hit tests and the server's exporters all
draw from those baked values — there is no width or opacity formula in
any of them — and partial erasing keeps them in every surviving piece.

**The brush table**, in words (the numbers are pinned by
`tests/shared/inkwidth.json`, whose `about` is the normative statement):

- **Pen** (no `brush`): `w = 1 + (p − 0.5)`, half the size at no
  pressure, one and a half at full, perfect-freehand's on-screen value at
  thinning 0.5. Tilt is ignored.
- **Ballpoint** (`monoline`): no `w`; the line is the size everywhere.
- **Brush** (`brush`): `w = 2p`, shaped by two tapers along the stroke's
  arc length — a start taper two sizes long and an end taper three sizes
  long, each at most half the stroke's length, both eased out as a sine —
  and kept between 0.1 and 2.0. So a mark shorter than the two tapers
  (a comma) still reaches `2p` in its middle, and a stroke of no length
  (a tap, a dot) is not tapered at all and draws `2p`, as perfect-freehand
  draws a lone point at its untapered radius. The stroke in progress keeps
  the full start taper (its length is not known yet) and has no end
  taper; both are settled at the lift. When the pointer reports no
  pressure (a mouse or finger, or the pressure preference off) the
  pressure is simulated from the spacing of the samples with
  perfect-freehand's `simulatePressure` step (rate 0.275 from 0.5; not its
  averaging over the first ten points, which sets its starting value)
  — slow is wide, fast is thin, time is not read — so a mouse brush
  stroke carries `w` without `p` and the only felt look without a
  stylus. The spacing is read in page points as the samples are, so the
  same hand movement draws a mouse Brush wider zoomed in than zoomed out,
  and wider with a mouse that reports more often.
- **Pencil** (`pencil`): `w = (1 + (p − 0.5)) × (1 + 2 × (1 − a / 90))`,
  the pen's width grown by up to three times as the altitude `a` falls
  from upright (90°, the default when no tilt was reported) to flat,
  capped at 4; and `o = 0.15 + 0.85p`, the opacity multiplier, written in
  the `o` channel — the pencil is the first brush that writes it. A mouse
  or finger pencil stroke writes neither.
- A **highlighter** has no brushes and no `w`; a **mouse or finger** Pen
  or Ballpoint stroke (`pen: false`) is even, as today.
- A stroke that carries a **`shape`** record (a held shape, a straight
  highlight, and later the shape tools) is **constant width whatever its
  brush**: no law runs on it, so it has no `w` and no `o` and draws its
  size end to end. It keeps its brush key, so the Style picker still
  names it and a restyle changes it, and it is written `xyt`. (The
  Brush's tapers would put both samples of a held straight line at the
  0.1 floor, and every server renderer draws between a stroke's samples:
  the exports and the AI's picture would show a hairline.)

**Dash geometry** is never stored: a stroke's `dash` is `dashed` or
`dotted` and the pattern comes from its `size` — dashed: on two sizes,
off two sizes; dotted: on a tenth of a size, off two sizes; round caps,
phase 0 at every stroke's and every cut piece's first sample
(`tests/shared/inkdash.json`). A constant-width stroke is dashed as a
stroked line; a pressure stroke is cut by arc length into on-runs that
are each outlined, and a dotted pressure stroke is a circle per dot of
the drawn diameter there.

The storage shape is a deliberate copy of what the two
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
| `brush` | `^[a-z][a-z0-9-]{0,15}$` | omitted | `monoline` (Ballpoint: constant width, no `w`), `brush` (Brush: `w` from pressure or speed with tapers) and `pencil` (Pencil: `w` from pressure and tilt, `o` from pressure) are known; a stroke with a `shape` keeps its brush but has no `w` or `o`, whatever the brush; an unknown brush draws exactly as a pen from `w` (and `o`), so only texture is lost. Open on every tool: the server does not refuse one on a highlighter (it draws at its size, as any highlighter) |
| `color` | `#rrggbb` or `rgb[a](…)`, ≤ 40 chars | `#1f1f1f` | |
| `size` | number in (0, 100] | `1.6` | nominal diameter in pt, the anchor `w` multiplies; a width restyle changes `size` only, a resize scales it; the server refuses `size × max(w) > 100` |
| `opacity` | number in (0, 1] | `1` | |
| `pen` | bool | `true` | `false` marks a stroke without real pressure — a mouse or finger, or a stylus with *Pressure-sensitive strokes* off — drawn even, no `w`, for every brush but `brush`, whose `w` then comes from the pointer's speed (a snapped stroke is `pen: false` too, and even whatever its brush: it carries `shape`) |
| `t0` | int ≥ 0 | omitted | wall-clock ms of the first sample, never invented (a client without timing omits `t` and `t0`); the replay reads both |
| `band` | `0` or `1` | the tool's default, `0` for a highlighter and `1` otherwise, and omitted when equal to it | `0` draws under, `1` draws as ink. Every layer draws band 0 first, then band 1 (`InkLayer`, `to_svg`, `pdf_path_ops`, the annotated PDF's `/Annots` order); a tap prefers band 1 within the radius (`nearestInkStroke`) |
| `from` | a stroke id | omitted | the stroke this piece was cut from by the partial eraser. A cut of a piece keeps the root's id, so every piece names the original. Written by `restroke`, kept by every edit, dropped on paste and duplicate (`cloneStrokes`, `duplicateStrokes`: a copy is not a piece). Read by the merge only |
| `shape` | `{kind, params}` | omitted | `kind` one of `line` `arrow` `rect` `ellipse` `triangle` `polygon` `curve`; `params` an object of at most 64 finite numbers (and booleans), the geometry an editor rebuilds `pts` from after a handle drag, moved with `pts` by every edit that moves the samples ("Client"). The stroke **also carries its baked `pts`**, so hit test, bounds, eraser, lasso, replay and every exporter draw it as any stroke; it is constant width whatever its `brush` (no `w`, no `o`: "The brush table"); a piece cut from a shape loses `shape` and `fill` |
| `fill` | `{color: '#rrggbb', opacity}` | omitted; `opacity` in (0, 1], omitted at `0.25` | closed shapes only |
| `dash` | `dashed` or `dotted` | omitted (solid) | dash geometry is derived from `size` at render time, never stored ("Dash geometry" above; `DASH` in `ink.js` and `ink.py`) |
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
| `w` | drawn-width multiplier × 1000 | delta after the first sample; the running sum is checked to 0..4000 (decoded 0..4.000, `1000` = the nominal size; the ceiling was 2000 before the pencil, whose slant reaches 3.5, and the drawn ceiling stays `size × max(w) ≤ 100`) |
| `o` | drawn opacity × 1000 | delta after the first sample; running sum 0..1000 (decoded 0..1.000); written by the pencil. The rule is `opacity × o` (`strokeOpacity` / `stroke_opacity`, one line each, like the width, pinned by `inkwidth.json`); the renderers draw it in three shades, `o` rounded up to 0.33, 0.66 or 1 (`opacityBand`, `stroke_bands`), each shade at `opacity × band` |

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
in `ink.py`, both one line); the opacity is `opacity × o` with the `o`
channel, else `opacity` (`strokeOpacity`, `stroke_opacity`, the same one
line), which every renderer draws in three shades (`o` rounded up to a
band, "Channels" above), so no law hides there either. Pressure-shaped
strokes — tool `pen` with `pen` true and no `brush`, or brush `brush` or
`pencil`, and no `shape` — always carry `w`, baked once on
the client in `finishStroke.js` `bakeWidths` from the brush table
(`BRUSHES`, "The brush table" above), the only place a width or opacity
law lives; a brush restyle of existing strokes runs the same function
again (`restyleBrush`, "Client"). Constant-width strokes (highlighter,
monoline, a `pen: false` Pen, Ballpoint or Pencil, and every stroke with a
`shape`, whatever its brush) omit `w`; a freehand `pen: false` Brush
stroke carries `w` from the speed law and no `p`. The raw
`p`, `a`, `z` stay beside `w` (Notability keeps force and tilt beside its
fractional widths), so a brush change re-bakes from them and a future law
can re-map; the multiplier form means a width restyle touches `size`
alone and `pts` stay byte-identical. perfect-freehand is driven from `w`:
pressure = `w / 2` with thinning 1 (perfect-freehand does not clamp the
pressure, so a pencil's `w` of 3.5 draws a radius of 1.75 sizes), and
the outline's radius matches the baked width exactly; the live
canvas runs the same function on the samples' live `w`, computed by the
same law (a brush's end taper is applied at the lift, so the wet line has
its start taper only). Tilt is captured for pen pointers only
(`inkSample`: `altitudeAngle` / `azimuthAngle`, else `tiltX` / `tiltY`,
as integer degrees), and the `a` / `z` letters are written only when at
least one sample of the stroke reported tilt (samples without it inside
such a stroke write 90 and 0, the altitude clamped to 0..90, in both
codecs: `encodePoints`, `encode_points`), so a tilt-less client and a tilt-capable
one hash an untilted stroke identically. The iPad shell sends raw samples
and the web app bakes them like its own ([ipad.md](ipad.md) "The pen").

**Limits** (`gamma/ink.py`, enforced on upload): 5 000 strokes, 500 000
samples, `MAX_BYTES` 6 MB, finite numbers, unique stroke ids, the extras
caps, `w` in 0..4, `o` in 0..1, `size × max(w) ≤ 100`.

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
  `size`, `shape` and `brush`; its **look** is everything else but `id`
  and `from`: `color`, `opacity`, `dash`, `nib`, `band`, `fill`, `tool`,
  `pen`, `t0` and any extra key. A width restyle is therefore geometry, a
  move or a resize too, and so is a brush change: it bakes `w` in `pts`
  again, so the key travels with the widths baked for it, and a brush
  change here composes with a recolour there into the new brush with its
  widths in the new colour, while an undo of it after someone's recolour
  gives the plain widths back with the key. (As a change of both halves
  it would split: the recolour's side kept the old key on the new
  widths, the undo the new key on the old ones.)
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
  while writing goes on (so files land during a session), and flushes at
  once with `fetch` `keepalive` when the tab goes to the background
  (`visibilitychange` to hidden: a phone may freeze or end a hidden tab
  without a `pagehide`). **Every group's requests start at once**, none
  waiting for another group's: nothing is sure to run once a closing page
  is gone, so a flush waiting for each group in turn would send the first
  alone. The groups **share the room the page's keepalive ledger leaves
  handwriting**: browsers carry at most 64 KiB of keepalive request
  bodies in flight per page and refuse a keepalive fetch past that, so
  every keepalive request of the page goes through one ledger
  ([collab.md](collab.md) "Keepalive"), and handwriting comes last. Its
  room (`available('low')`) is the 64 KiB less what the page has in
  flight, less what the queued page edits claim (they have no other copy
  and go on `pagehide`), less 8 KiB kept for the small requests (the
  profile, the reading position). `keepalive` goes to the groups, in
  order, whose upload and `PUT` bodies (their UTF-8 bytes) still fit in
  that room; a group that does not fit goes as plain fetches, and a
  smaller one after it may still fit (`ink/inkFlush.js` `planFlush`,
  "Client"). A plain fetch may still land, but a closing page may cut it
  off: then the draft journal is what keeps that handwriting, restored on
  the next visit ("Client", the journal). That journal is why handwriting
  is the lowest priority: typed edits lost at the close are lost for good.
  A closing tab fires `visibilitychange` before `pagehide`, so
  `pagehide` flushes every draft still unsaved again, whether or not the
  first flush is still on its way, and there the upload and the block's
  `PUT` go side by side, the `PUT` naming the file by the hash `inkStore`
  worked out when the draft changed (`nameLater`, idle time), since
  nothing is sure to run once the page is gone. Only there: a peer that
  the `PUT`'s op reaches before the upload has landed finds no file, so a
  flush the page will live past keeps the upload-then-`PUT` order, and so
  does a draft the server will have to merge (its block names another
  file than the draft's base) even on `pagehide`. A drawing whose upload
  the first flush has on its way with keepalive is not uploaded again:
  `pagehide` sends its `PUT` alone, beside that upload, or nothing when
  the `PUT` cannot go beside it (then that flush sends it if the page
  lives, and the journal brings the drawing back if it does not); one
  whose `PUT` is on its way with keepalive sends nothing more, and an
  empty group whose `DELETE` is on its way is not deleted again (a second
  would be answered 404 and raise "Handwriting not saved yet" on a page
  the back-forward cache keeps). The `DELETE` has no body, so it goes with
  keepalive, and is held as on its way, even when the queued edits leave
  handwriting no room at all. Every `PUT`
  is written to the draft journal before it goes (`inkStore.markSending`,
  "Client"), since its answer may never come. The flush **stops retrying
  on a 4xx** (`collabSession.retryableStatus` decides): the draft is kept
  and a persistent pill says "Handwriting not saved: <detail>". 5xx and
  network errors keep the 2 s retry.
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
  the merge and the rendering. **The eraser** works on the path it
  swept: `eraseAlong(ink, path, radius, allow)` is the partial eraser
  along `path` (page points, consecutive points the capsules of the
  radius, one point a disk), cutting every stroke where its centre line
  comes within the radius plus the drawn half-width there (`strokeWidth`
  / 2, linear between two samples). The test runs along each segment
  between two samples, against each capsule (`underCapsule`): the
  distance from a point moving along a segment to a segment is convex,
  and less a linear half-width it stays convex, so their common part is
  one interval of the segment, found from a point inside it (where the two
  segments cross, or an end of one against the other, else a
  golden-section search) by a bisection to each side to 0.001 pt, ending
  just outside the edge; boxes (the stroke's, each capsule's, each
  segment's) rule out nearly every pair first, and an overlap shallower
  than the file's 0.01 pt grid is a touch and cuts nothing: a cut end
  rounded onto that grid may lie about that far inside the edge, and a
  sweep over it again (the next flush starts where the last one ended)
  must leave it as it is. The intervals of all capsules are merged
  (`sweptRanges`), and the stretches between them become
  pieces through `restroke`, each new end a sample lerped where the
  stretch ends (`sampleAt`: `x`, `y`, `p`, `t`, `w`, `o` linear; tilt and
  letters this build does not know from the nearer sample), so a piece
  ends at the eraser's edge however far apart the stroke's samples or the
  path's points lie; a piece shorter than max(0.2 pt, half its drawn
  width at either end) is a speck and goes, and a dot (one sample) within
  reach goes whole. → `{ink, changed}`; a stroke `allow` refuses stays the
  same object, and a sweep that cuts nothing gives the same ink.
  `eraseAt(ink, x, y, radius, allow)` is it at one point.
  `hitStrokesAlong(ink, path, radius, keep)` is the whole-stroke eraser's
  test on the same geometry (the strokes the partial eraser would cut),
  `hitStrokes(ink, x, y, radius, keep)` at one point.
  `frontend/tests/inkErase.test.mjs` pins the cuts, and
  `frontend/tests/bench/inkErase.bench.mjs` (not part of `npm test`)
  times one flush of a 20-point path over a 500-stroke drawing, against
  any copy of `src/ink` (one without `eraseAlong` is run as its layer
  ran it, the point eraser at each of the path's points), and counts the
  samples a flush leaves inside the swept band. `translateStrokes` only
  touches the first sample's two
  absolute integers (and a shape record's points, below); `transformStrokes` scales/rotates selected XY samples
  around a shared origin without changing the other encoded channels;
  `strokesInLasso` picks strokes with more than half their samples inside
  the polygon. A pen stroke (any brush) renders as perfect-freehand's
  outline in one filled SVG path (page units; the layer's `viewBox` does
  the zoom), driven by the baked `w` (`outlineOptions`, `outlinePressure`);
  a highlighter as a stroked polyline with `mix-blend-mode: multiply`; an
  unknown tool as a source-over polyline at its mean drawn width
  (`strokePath`). **Dash**: `DASH = {dashed: {on: 2, off: 2}, dotted:
  {on: 0.1, off: 2}}` in sizes; `dashPattern(stroke) → [on, off] | null`
  in points; a stroked path gets `strokePath(...).dash`, the
  `stroke-dasharray` string (`"4 4"`), and a pressure stroke's `d` is
  built by `dashRuns(samples, stroke) → [[samples], …]` — the on-runs by
  arc length with the boundary samples lerped (`x`, `y`, `p`, `t`, `w`;
  `a`, `z` from the sample before), each run outlined on its own,
  round at both ends, the outlines concatenated — or, dotted, a circle of
  diameter `size × w` at each run's middle (`tests/shared/inkdash.json`
  pins the runs). A piece of a stroke is outlined by
  `runOutline(samples, opts, first, last)`: perfect-freehand at ten times
  the page's scale, a two-sample piece with its midpoint added first, as
  the server's `stroke_outline` does — perfect-freehand drops the points
  within 3 units of a path's end, so a piece shorter than 3 pt (a thin
  pen's dash, a short shade) would outline as a disc about its length
  across, and it draws the points it puts between two at pressure 0.5,
  the nominal size. Every dash of a dashed pressure stroke is such a
  piece, the only dash of a stroke shorter than one dash and a gap
  included; a solid stroke is outlined whole at the page's scale
  (`penOutline`). The live canvas outlines its dashes the same way, its
  first while it is the only one too. A
  **pencil** stroke's `d` is three paths, one per
  opacity band — `o` quantised up to 0.33, 0.66 or 1 (`opacityBand`),
  consecutive samples of one band forming a run with lerped boundaries
  like a dash (`bandRuns`, which leaves out a run of no length, as the
  server's `stroke_bands` does) — each filled with the layer's grain pattern at
  `fill-opacity` band × `opacity` (`strokePath` returns `{bands: [{d,
  alpha}]}` for it, the runs' inner ends flat so the shades meet without
  overlapping; every pencil's shape, a mouse pencil's even outline
  included, carries `grain: true`, which the layer fills with the grain;
  `strokeOpacity(stroke, sample)` is the exact opacity rule beside
  `strokeWidth`, which the bands round and the fixtures pin, and
  `strokeMaxWidth(stroke)` the widest it draws, what
  the server caps at 100 pt — the selection's resize stops there and
  `restyleBrush` holds a re-baked `w` there). **Caches**: decoded samples,
  bounds and outlines are kept per `pts` array (`strokeCache`, a WeakMap
  keyed by the array, with a WeakMap per stroke object in front), each
  value also keyed by what else it was built from — the size for the
  bounds; every key but the colour, `id`, `from` and `t0` for an outline
  (`outlineKey`), so an outline never reads the colour — and reused while
  the stroke keeps that array: a colour restyle, `withDefaults`, the
  merge's composed and untouched strokes, and a reload whose strokes
  `inkStore.adoptStrokes` matched by id. A move, a cut or a brush restyle
  writes a new array and builds again, and so does a change of size, line
  or opacity on the same array. `cachedStrokePath(stroke)` looks without
  building, and `strokePlaceholder(stroke)` is the stand-in a dense group
  draws meanwhile (the samples as a stroked polyline at the mean drawn
  width). `frontend/tests/bench/inkCache.bench.mjs` (not part of `npm
  test`) times the outlines of a 500-stroke drawing cold and after a
  reload, a merged save and a restyle, against any copy of `src/ink`.
  `nearestInkStroke` resolves a touch to the nearest stroke edge (a band-1
  stroke wins over a band-0 one within the radius, then the topmost);
  `restyleStrokes(ink, ids, patch)` changes selected colour, `size` or
  `dash` (`{dash: 'dashed' | 'dotted' | null}`, null clearing it; any
  other value is ignored; never `pts`), returning the original object for
  a no-op — a brush change is `finishStroke.js restyleBrush`, since it
  re-bakes; `cloneStrokes(strokes, dx,
  dy, used?)` is the copy every paste and duplicate is made of: fresh ids
  (none in `used`), `from` dropped, moved by `dx, dy` pt through the
  first sample's two absolutes, every other key — `t0`, `p`, `t`, `w`,
  `shape`, extras — kept; `duplicateStrokes` is it on a selection.
  **A shape record follows the samples**: every edit that moves them —
  `translateStrokes`, `transformStrokes`, `cloneStrokes` (so
  `duplicateStrokes` and a paste), `flipStrokes` — carries the stroke's
  `shape.params` through the same map (`mapShape`), so the params an
  editor rebuilds `pts` from (`shapes.js` `shapeSamples`) never snap a
  moved shape back: its points (`cx, cy` and every `xN, yN` — a line's or
  an arrow's ends, a triangle's corners, a curve's points) moved, turned
  and scaled as the samples are, its lengths (`w`, `h`, `r`, `rx`, `ry`,
  `head`) times the scale, its `angle` turned (or mirrored) and put back
  in the range `recognise` gives it (a rectangle's [−45°, 45°) with its
  sides swapped across a quarter turn, an ellipse's [0°, 180°), a
  polygon's [0°, 360° / n)), every changed number on the 1/100 grid; a
  side count, a flag or an unknown key stays as it is. A key the record
  leaves out is written with what `shapes.js` draws in its place when the
  edit changes it: an arrow's head (`min(length / 4, 24)`) times the scale
  on a resize, the angle 0 of a rectangle, a polygon or an ellipse on a
  turn or a flip. A record of a kind
  this build does not know is dropped by such an edit, the stroke kept as
  ink: its params may be geometry it cannot move, and stale ones would
  rebuild the wrong samples. An edit that moves nothing keeps the record,
  the same object. `frontend/tests/ink.test.mjs` rebuilds every kind from
  its params after each edit and finds it within 0.5 pt of the moved
  samples.
  `appendStrokes(ink, strokes)` adds strokes to a group as they are (a
  paste's copies, a merge's moved strokes, `from` and all), re-minting
  only an id the group already holds, → `{ink, ids}` as they landed.
  `flipStrokes(ink, ids, {cx, cy, axis})` mirrors the selected strokes
  about `(cx, cy)` — `'h'` rewrites each `x` as `2cx − x`, `'v'` each `y`
  as `2cy − y` — in the samples alone (size, `p`, `t`, `w`, ids and
  channels stay), carries a `shape` record through the same reflection
  (`mapShape`: points and centres reflected, an angle `180° − angle` for a
  horizontal flip and `−angle` for a vertical one, re-normalised as
  `recognise` returns them) and a `nib` as `(180 − nib) mod 180` (the same on either
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
    result at the lift, `{kind, params} | null` (under the iPad host, the
    shape the claim kept, followed through the final samples); `straight` the
    highlighter preset's `'hold' | 'always' | 'off'` (`undefined` reads
    as `'hold'`); `scribble` whether scribble-to-erase is on; `groups` the
    page's groups, `[{id, ink}]`, and `keep(stroke) → bool` the eraser
    filter, both for the scribble stage. Every key but `style` is
    optional, so the existing calls stand.
  - The stages, in this order, each returning as soon as it decides:
    1. **hold**: `ctx.hold` set → `commit = shapeSamples(kind, params,
       {span: last t − first t, p: mean p})` (page units, `t` from 0 over
       the span), `style = {...ctx.style, pen: false, shape: {kind,
       params}}` (an even line in the preset's colour, width, brush and
       line, and constant width whatever the brush: `widthLaw` and
       `opacityLaw` are null for a style with a `shape`, so nothing is
       baked — no Brush tapers, no Pencil shading — the brush key stays and
       the stroke is written `xyt`;
       the `shape` key rides into `encodeStroke` as an extra; a `circle` is
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
       table `BRUSHES`, the only place a width or opacity law lives —
       `{pen: {w: law}, monoline: {}, brush: {w: law over the whole
       stroke, simulate: the speed law}, pencil: {w: law, o: law}}`, read
       through `widthLaw(style)`, `opacityLaw(style)`; `bakeWidths(samples,
       style)` writes `w`, and `o` for the pencil, rounded to three
       decimals, running the brush's tapers over the samples' arc length
       (each at most half of it, none on a stroke of no length) and its
       speed law when the style has no pressure (`pen: false`, or
       samples without `p`); a style with a `shape` has no laws, so a
       snapped stroke is never baked;
       `brushWidths(samples, style, {live})` and `brushOpacities(samples,
       style)` are the same values as arrays, `live` for a stroke still
       being written (the live canvas, a peer's wet stroke): the end taper
       left out and the start taper at its full length, whatever the
       stroke's length so far, so the line under the pen holds still;
       `strokeChannels(style, samples)` is the `ch`
       a new stroke is written with: `xyptw` for a pressure pen or brush,
       `xyptwo` for a pencil, `+az` after `t` when any sample reported
       tilt (`xyptazwo`), `xypt` for a monoline or a highlighter, `xyt`
       for a mouse or finger stroke and for every snapped result (`pen:
       false` and a `shape`, whatever the brush), except a mouse brush's
       freehand stroke, `xytw`):
       `{commit, erase: null, label: T("ink stroke"), style: ctx.style,
       status: null}`.
  - `restyleBrush(ink, ids, brush) → ink`: the selection's brush change.
    For every selected stroke of tool `pen` (`brush` `null` or `'pen'`
    for the plain pen, else `monoline` | `brush` | `pencil`; any other
    value is ignored) it decodes the samples, strips `w` and `o`, bakes
    them again for the new style (the stroke's own `pen` flag, so a mouse
    stroke turned brush gets the speed law, and its `shape`, so a snapped
    stroke stays constant width: only its brush key changes, and a `w` or
    `o` an older build baked for it goes), re-encodes
    with the stroke's own channels but `w` and `o` plus what the new brush
    writes — `strokeChannels` for every stroke this build writes, while a
    letter this build does not know keeps its place — and writes the new
    `brush` key (dropped for the plain pen). A stroke already in that
    brush whose widths and shades are within `BAKE_SLACK` (0.05) of the
    ones baked again is the same object (the bake at the lift read the
    samples before the file rounded them to 1/100 pt, which moves a
    Brush's tapers by up to about 0.02), so choosing its brush again
    changes nothing; one whose key and widths disagree is baked again.
    Highlighters are the same objects; the same `ink` object comes back
    when nothing changed.
    Lives here, not in `ink.js`, because the bake does, and `ink.js`
    never imports upward.
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
  - The hold's numbers, one table for the layer and the iPad host (whose
    Swift constants mirror it): `HOLD_MS` 600, `HOLD_MOVE` 3 css px (a tip
    that moves farther starts the timer again), `HOLD_MIN_MS` 250,
    `REFIT_PX` 6, `CANCEL_PX` 12, `CANCEL_MS` 150 and `PASTE_MOVE` 2.
  - `stillSince(samples) → index` (fixture `tests/shared/inkhold.json`,
    which the iPad app's XCTest runs too): the stillness anchor of `[{x,
    y}]` in css px, the latest sample more than `HOLD_MOVE` from the
    anchor before it, the first sample to start with. A hold fires
    `HOLD_MS` after the anchor's sample, so the snap's time is that
    sample's `t` + `HOLD_MS`.
  - `followHold(hold, samples) → params | null` (the same fixture): the
    shape a hold under the iPad host comes to, the rule the shell runs
    too (`ShapeHold`), over `hold = {kind, params, from, n, tSnap}` and
    the stroke's `[{x, y, t}]`, all in the host's css px. Of the samples
    from index `n` on, the first one more than `CANCEL_PX` from `from`
    while `t − tSnap ≤ CANCEL_MS` drops the shape (null); once one has
    been `REFIT_PX` from `from` the answer is `refitShape(kind, params,
    {pointer: the last sample, from})`, else `params`.
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
  and kind in words); `{kind: "brush", step: ±1}` for the same keys with
  Shift (App steps an armed pen preset through `BRUSH_ORDER` — `null`
  (Pen), `monoline`, `brush`, `pencil` — wrapping, and raises the same
  pill, whose `name` now says the style, "Blue brush pen · 2 pt."; an
  armed highlighter or no preset ignores it; a widget key, not a catalog
  command, like the brackets themselves); `{kind: "eraser", phase: "down"
  | "up", held}` for
  `E` — `down` on a non-repeat keydown, `up` on the keyup with `held` the
  ms since the down, so App decides tap or hold ("Hold-E" under App);
  `{kind: "escape"}` resolved by App in order — `hasBox` leaves it to the
  box (null), `hasSelection` → `deselect`, `optionsOpen` → `closeOptions`,
  else `close`; `delete`; `undo`. Nothing with Mod-C / X / V / D / A: those
  are catalog commands.
- `ink/pencilActions.js` (pure): `pencilAction(message, prefs, state)`,
  the Pencil's double tap and squeeze as App applies them, and
  `backTool(prefs, state)`, the tool to go back to (the one armed before,
  when it is still on the strip and not the current one, else the stylus
  pen), which the gestures' *Switch to last tool* and App's `inkBack`
  both read.
  - `message`: `{gesture: 'tap' | 'squeeze', action, phase?: 'began' |
    'ended' | 'cancelled'}` — the host's `pencil`, and its `squeeze` as
    the gesture `squeeze`; a bare action string reads as a tap. A squeeze
    in any other phase (a later shell's `changed`) answers null.
  - `prefs`: `{tools, pen, tap, squeeze}`, the last two this browser's
    `inkPencilTap` and `inkPencilSqueeze`. `'system'` (the default) maps
    the system's action: `switchEraser` → `'eraser'`, `switchPrevious` →
    `'previous'`, `showColorPalette` and `showInkAttributes` →
    `'options'`, anything else → `'none'`. `'eraser'` toggles the eraser
    against `backTool`, `'previous'` picks `backTool`, `'options'` opens
    the armed tool's row (with the hand armed, the stylus pen's),
    `'undo'` undoes and `'none'` does nothing; a squeeze also takes
    `'holdEraser'` and `'holdPrevious'`.
  - `state`: `{tool, previous, options, busy, squeeze, now}` — `busy` a
    stroke or a claim in progress (`InkLayer.jsx` `inkBusy()`), `squeeze`
    the squeeze App holds open (`{since, ignored, hold: {tool, used} |
    null}`, or null), `now` in ms.
  - The answer: `{pick: id}` (arm it; null is the hand), `{ui: patch}`
    (the strip's state), `{undo: true}`, `{began: {ignored, pick?}}` (a
    squeeze started; `pick`, only for a hold choice not ignored, is the
    tool to arm while held, what the choice's toggle would arm: the
    eraser, or `backTool` with the eraser armed already, so a short
    squeeze toggles back; `backTool` for `'holdPrevious'`), `{release: {pick}}`
    (a held squeeze ended or was cancelled: back to `pick`, the tool armed
    when it began, null the hand) or `{release: {stay: true}}` (after a
    squeeze under `SQUEEZE_TAP_MS`, 300, that neither erased nor drew: the
    tool it armed stays), or null. A message without `phase` acts at
    once, a hold choice as its toggle (`'eraser'`, `'previous'`);
    `began` answers for every choice, so its end can tell; `ended` acts
    for the other choices only after a `began` that was not ignored;
    `cancelled` acts only for a hold. While `busy` nothing acts but a
    hold's end, and a `began` comes back `ignored`.
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
  which the save sends as `base_ids`. A file loaded again for a block —
  the one a peer's save names, the merged file a save comes back with —
  goes through `adoptStrokes(file, onScreen)`: by stroke id, a stroke
  equal to the one on screen is that object, and one with equal samples
  keeps that `pts` array, so the outline caches survive the reload
  (`inkFor` hands `loadInk(url, known)` the drawing it last showed for the
  block). `inkShown(block)` is what a layer and a card draw: `inkFor`'s
  drawing, or the one shown last while the block's new file is on its way,
  so a peer's save never blinks a group out; edits read `inkFor`, which
  waits for the file they change. **The journal** (`ink/inkJournal.js`,
  IndexedDB database `gamma-ink-journal`, store `drafts`, key
  `<workspace>/<block id>/<tab>`, value `{bytes, base, baseIds, at, tab,
  sent}` — the dirty draft's canonical `serializeInk` bytes, the file it
  was edited from and that file's ids, when, the tab that wrote it, and
  the uploads whose block `PUT` that tab sent without an answer yet,
  oldest first): `inkStore` writes a draft's entry when it turns dirty
  (300 ms after the edit), at once when a `PUT` goes (`markSending`,
  awaited, for half a second at most, before the `PUT` of a flush the
  page will live past) and on
  `pagehide` (`journalAll`), takes a `PUT` off `sent` when its answer is
  an error (`markUnsent`: it did not land), and deletes the entry when
  the upload lands (`markSaved` with nothing drawn meanwhile, which also
  answers every `PUT` sent before it; `markDeleted`) or the block starts
  over (`dropDraft`). So an entry that outlives its tab is handwriting a
  flush never landed, or one whose `PUT` may have landed with its answer
  lost (a `pagehide` flush, a tab frozen in the background). The key
  names the tab, so two tabs drawing in one group, or a tab drawing in a
  group before the restore of a closed tab's entry ran, never write over
  each other. A tab holds a Web Lock named after itself
  (`gamma-ink-tab-<tab>`) while it lives. The workspace's entries are
  read once, at the first block shown, through a key range on the
  workspace's prefix (no other workspace's entry is loaded), and one
  older than 7 days is deleted as it is read, so an entry whose block
  never comes back goes with time. A block with entries is restored the
  first time it is shown, one entry a visit, unless this tab has a draft
  of it (the entries wait for the next visit), the entry is this tab's
  own, or its tab's lock is still held (that tab is open and saves it
  itself). What the entry's change counts from is the newest of its
  `sent` uploads that the server has — that `PUT` may have landed, so
  what it carried is the server's now, to keep or delete — else the
  entry's `base`. (The upload alone would not tell: a flush the tab did
  not live through may have stored the upload and never sent the `PUT`;
  within the journal's 7 days a stored upload is never purged, the
  unreferenced ones being kept 30, [user_db.md](user_db.md) "Stored
  files".) The side this errs on is the server's: a `PUT` that left
  after its upload landed and never landed itself, its connection gone
  under it, counts as landed too, so what only it carried is not
  brought back. `restoredInk(entry, {url,
  current, base, baseUrl, lost})`
  decides, pure: when the file it counts from is the block's current
  `ink_url` the entry is the drawing as it is; when the block names
  another file (someone saved meanwhile) and that file still loads,
  `mergeInk(base, entry, current)` less every stroke `base` had that
  `current` lacks, and the pieces cut from one (mergeInk lets an edit
  beat a delete, which suits two writers at work, not a tab that is
  gone); nothing when that differs in no stroke from the current file,
  when the base is gone (a 404), or the entry is older than 7 days, and
  the entry is dropped — so a journal never brings back a stroke the
  server has deleted since the closing tab last reached it, unless the
  browser lost the journal's own last write as the page went. One block
  file that is gone is the entry's own: the pagehide flush sends the
  block's `PUT` beside the upload, naming the file by its hash
  ("Fences"), and a `PUT` that landed while the upload did not (a group
  past handwriting's keepalive room goes as plain fetches, and the
  closing page may cut off the upload and not the `PUT`) leaves the block
  naming a file the server never got; when
  the block's file is a 404 and its name is one of the entry's `sent` or
  the hash of the entry's bytes (`lostUpload`), the entry is that
  drawing, restored whole (`lost`), and its save uploads it again. A
  base or block file that fails to load otherwise leaves the entry for
  the next visit. A block whose `ink_url` changes while its restore runs
  (inkStore keeps the URL each block was last asked for with) is restored
  again against the new file, so *Discard* reverts to the file the
  drawing was really laid over. A restored drawing becomes the block's
  dirty draft, based on the file the block names now, its entry this
  tab's in the same write that deletes the closed tab's (`moveEntry`),
  flushed like any edit, and is reported once through
  `onInkRestored(fn)` → `fn({id, ink, prior, ids})` (queued until App
  listens), which App turns into the pill "Unsaved handwriting from a
  previous visit was restored." with *Discard*: the restored change taken
  back — `mergeInk(ink, prior, now)`, the restored drawing to the file it
  was laid over, applied onto the group as it is now, as an undo is — as
  one `applyInk` entry, "ink restore discarded", so it is undoable in
  turn. Every IndexedDB call is wrapped: a browser without it, or a
  private window, loses only the journal; `frontend/tests/inkJournal.test.mjs`
  runs it on an in-memory IndexedDB.
- `ink/inkFlush.js` (pure; `frontend/tests/inkFlush.test.mjs` is the
  proof, since the e2e cannot drive a `pagehide`): what App's `flushInk`
  sends for each dirty group, and which of it goes with `keepalive`
  ("Fences"). `planFlush(groups, budget) → [{id, send, keepalive}]` takes
  the groups in the flush's order, each `{id, remove, uploadBytes,
  putBytes, beside, inFlight}`: `remove` a group erased empty with nothing
  under it (a `DELETE`, no body); the UTF-8 bytes of its upload's body and
  of its block `PUT`'s (sized with the upload's name, or with
  `inkStore.UPLOAD_NAME_CHARS` while it is not named yet); `beside` its
  `PUT` may go beside the upload (`pagehide`, the upload named, no merge
  pending); `inFlight` `'upload'`, `'put'` or `'delete'` when a keepalive
  request of this very drawing is on its way from an earlier flush.
  `send` is `'delete'`, `'both'` (the upload and the `PUT`: side by side
  when `beside`, else the `PUT` after the upload's answer), `'put'` (the
  `PUT` alone, beside the upload on its way) or null (its `DELETE` or its
  `PUT` is on its way, or its upload is and the `PUT` cannot go beside
  it). `keepalive` goes to each group, in order, whose bytes
  (`uploadBytes + putBytes`; `putBytes` for a `'put'`; 0 for a `DELETE`,
  which fits even a room of 0) fit in what is left of `budget`: App
  passes the room the page's keepalive ledger leaves handwriting
  (`shared/lib/keepalive.js` `available('low')`, never below 0,
  "Fences"; -Infinity for a flush the page will live past, which asks
  for none). App sends each request through the ledger
  (`apiJson`'s `{keepalive: 'low'}`), asking again when it starts — a
  `PUT` waiting for its upload's answer may find the room gone and go
  plain — and holds by drawing, until each settles, the upload, the `PUT`
  and the `DELETE` it has on their way with keepalive (`inkFlightRef`). A
  group that does not fit goes as plain fetches, and a smaller one after
  it may still fit. The bytes are the ledger's `bodyBytes`, a string's
  UTF-8 length, not encoded past the cap (a longer text's length, already
  past it, stands for it).
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
  Its first row is fixed — Color, Width, Style (`ctlBtn`, `BrushIcon`,
  aria-label and title "Style", `aria-expanded`, `modeActive` while its
  row shows), Copy, Duplicate, More (`ctlBtn`, `MoreIcon`, aria-label
  "More", title "More edits", `aria-expanded`), Delete — Style opens the
  `.inkEditOptions` row with aria-label "Selected ink style" (`options
  === "style"`, one of the three option rows): the `InkStylePicker`
  (`ink/InkStylePicker.jsx`) when the selection holds a pen stroke (its
  button names the brush every selected pen shares, else "Mixed",
  aria-label "Style: Mixed", title "Pen style"; its menu,
  `InkStyleMenu`, the same four rows, opens outside the `InkTooltips`
  host on the press like the strip's — a `ContextMenu` rendered inside
  the selection's, so a press in it never dismisses the selection menu
  and Escape closes it first ([ui-design.md](ui-design.md#menus-and-submenus))
  — and calls
  `onAction("style", {brush})` with `null` | `'monoline'` | `'brush'` |
  `'pencil'`), then the Line trio (`role="group"`, aria-label "Line",
  `aria-pressed` on a pill only when every selected stroke's `dash`
  matches it, calling `onAction("style", {dash})` with `null` |
  `'dashed'` | `'dotted'`), 32 px targets like the Color row's — and More
  shows the `.inkEditOptions.inkEditMore` row (its
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
    the pressure preference snapshotted at stroke start (off, a stylus's
    stroke commits as `pen: false`, like a mouse's: there is no pressure
    to read, so it is even, and a Brush follows the speed law), a pen's
    tilt (`penTilt`, integer degrees, none when upright), the pointer-up
    position with the last contact pressure and tilt (up reports zero),
    repeated points dropped. The live outline gets the same endpoint treatment as
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
    the `inkSelRect` blue, then the preset's look at its constant width,
    `law` null, as the shape commits: no Brush tapers, no Pencil shading),
    and once the tip has
    moved 6 css px every further move refits through `refitShape(kind,
    snapParams, {pointer, from, shift: e.shiftKey})`; a move of more than 12 css px
    within 150 ms of the snap clears `d.hold` and the freehand trace
    resumes. Shift with a mouse (`pointerType === "mouse"`) recognises at
    once. The timer is cleared in `handOver`, `finish` and `cancel`.
    `commitStroke` passes `hold: d.hold` to `finishStroke` and the
    result's `status` to `onHoldStatus`. A pen stroke that snaps calls
    `onHaptic('snap', tip)`, the tip in viewport CSS px, which App sends
    on only under a host.
  - The iPad host (`nativeHost().ink`, [ipad.md](ipad.md) "The pen"): a
    pen down claims the stroke (`claimForHost`) with the claim's tool,
    `claimTool(look, {k})` (`ink/hostInk.js`, below); the claim is
    `{taken, commit(samples, held), hold(samples), held}` in the module's
    `hostClaims`. Under `nativeHost().hold` the module also answers
    `hold` through the claim's `hold`: a claim not taken, or one keeping
    a shape already, answers `{}`; with `pasteArmed` and every sample
    within `PASTE_MOVE` of the first, the claim ends there (out of
    `hostClaims`, `hostOwned` cleared, its pointer `pasteHold`), *Paste
    here* opens at the down, and the answer is `{cancel: true}`; with
    `holdOn`, a chord of at least `max(24 css px, 18 pt)` and
    `HOLD_MIN_MS` from the first sample to the anchor (`stillSince`),
    `recognise` runs over the samples in page points (`hostEvent`, the
    draw context's `toPt`, its `kinds`; through the preset's stabilizer,
    the last sample closing the path, as the layer's own `snapHold` does
    with its tip), and a hit is kept as `held =
    {kind, params, from: the last sample, n: the samples' count, tSnap:
    the anchor's t + HOLD_MS}`, all in css px (`shapeToHost`), and
    answered `{shape: {kind, params}}`, with `onHaptic('snap', from)`;
    anything else answers `{}`. `commit` replays the samples through
    `hostEvent` (the tilt from degrees) and the stabilizer, as before,
    and with `held`, when the host's `stroke` names that hold (its `held`
    is `held.n`: the shell drew the shape), gives the draw context `hold:
    shapeFromHost(…)` of `followHold(held, samples)` (none when that is
    null) for `finishStroke`'s hold stage; a `stroke` naming no hold (its
    lift came before the shell had the answer, and it drew the freehand
    line) commits freehand. Under a host without `hold` nothing
    answers, so a captured stroke is never recognised.
  - `ink/hostInk.js` (pure): the iPad shell's side of a stroke.
    `claimTool(look, {k}) → tool`: `{kind, color, size: size × k,
    opacity}` and the look — `brush` for a pen of a known brush other
    than the plain Pen, `pressure: false` when the look reads no
    pressure, `dash` as `dashPattern(look)` × k (css px), `blend:
    'multiply'` for a highlighter, `stabilize` when the preset smooths;
    each left out at its default. `hostEvent(sample) → {clientX,
    clientY, pressure, timeStamp, altitudeAngle?, azimuthAngle?}`: a host
    sample, `[x, y, p, t]` or `[x, y, p, t, a, z]`, as the pointer event
    `inkSample` reads, the degrees turned to radians, so `penTilt` rounds
    and drops an upright pencil as it does a browser's. `shapeToHost(shape,
    {left, top, k})` and `shapeFromHost(shape, {left, top, k})`: a shape
    record between page points and viewport css px (`ink.js` `mapShape`:
    points moved, lengths scaled, angles kept, on the 1/100 grid).
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
    (the armed eraser, or the pen's eraser end / barrel button; both erase
    the same way), the down's mode (`'partial' | 'stroke'`) and radius in
    page units, a `pass` id minted at the down (`strokeId`), and `sweep`,
    the path not yet handed over: it starts at the down point and takes
    every coalesced position of every move, in page units (a repeat
    dropped). Once an animation frame the layer hands it to
    `onEraseAlong(page, path, r, {mode, pass})`, one call for all the
    page's groups, and keeps its last point as the next path's first, so
    the flushes join into one path; the lift hands over the rest at once,
    its own position included, and a cancel (`pointercancel`, lost
    capture, a second finger, window blur) what was swept up to it. The
    layer decides nothing about what goes: App tests the path against
    every group, the eraser filter included. `finish` in erase mode then
    calls `onEraseEnd()` once per gesture for `'tool'` only, whether or
    not anything was erased; a gesture ended by `pointercancel` does not.
    The hover ring draws a small pen or highlighter glyph when
    `ink.eraserFilter` is not `'all'` (the `.inkCursor`'s `data-filter`
    shows the matching `.inkCursorGlyph` in the centre mark's place).
  - The hover footprint (`showCursor`): the `.inkCursor` takes the armed
    tool's `data-tool`, and with `ink.hoverPreview` on a pen's
    `data-brush` (the teardrop), a pencil's leaning ellipse from
    `penTilt` (the pencil's width law at that slant, turned by the
    azimuth) and the preset's `data-dash` (`dashed` or `dotted`, the
    ring's border style); off, a round ring at the preset's width. The
    eraser's ring and glyph are the same either way.
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
  - The live canvas draws the stroke in progress from the brush table:
    the samples' live `w` by `widthLaw` (a brush's start taper only, at
    its full length; the end taper, a short mark's shorter tapers and the
    speed law's final values come with `bakeWidths` at the lift),
    `setLineDash(dashPattern)` for a constant-width dashed preset and the
    dashed runs for a pressure one, each outlined as a piece
    (`runOutline`), and for the pencil round **dabs** every `width / 4` of
    arc, each `size × w` across — the band the saved outline covers, so
    the line under the pen is the committed one; the lean is in `w`
    already, as in the saved stroke (an ellipse along the azimuth would
    draw a stroke written along it up to 60 % thinner than it saves) —
    filled with `createPattern(grainImage(colour),
    'repeat')` (scaled to `GRAIN_UNITS`, so it meets the SVG's grain),
    source-over, the dabs of one opacity band in one path filled once at
    `globalAlpha = opacity × band` — the shades the SVG draws — so dabs
    never darken where they overlap (`paintLiveStroke`, which a peer's wet
    stroke goes through too). **Dirty rects**: each frame of a stroke
    clears only the union of the last frame's box and this one's — the
    samples' (and the prediction's, the smoothing string's tip) padded by
    two sizes, the widest any brush draws — before the stroke is painted
    again (`paintStroke`, `canvasCtx(d, dirty)`), so a long stroke never
    clears the whole page's bitmap; the lasso clears whole, and peers' wet
    strokes have a canvas of their own.
  - `ink/grain.js` writes the grain tile, a 64 × 64 (`GRAIN_PX`) PNG of
    speckles at three strengths over 30 % bare paper (white noise over a
    finer paper tooth, from a fixed seed, so every client draws the same
    grain), as a 2-bit palette image: `grainUri(colour)` is the tile in
    that colour — only its palette differs, so no SVG filter tints it —
    and `GRAIN_URI` the black one, each built once; `grainImage(colour)`
    is the tile decoded for `createPattern` (null until decoded, and in
    node). The layer's `<defs>` hold one `<pattern
    id="inkGrain-<layer>-<colour>" patternUnits="userSpaceOnUse">` per
    pencil colour drawn on the surface, `GRAIN_UNITS` (32) page units
    square — two grains a point, so the grain zooms with the paper and a
    2 pt line is four grains wide — holding an `<image
    href={grainUri(colour)}>`; a pencil band fills with
    `url(#inkGrain-…)` (`GrainContext` hands `Strokes` the fill; a
    notes card's SVG has its own). One pattern per colour per SVG, never
    per stroke; a mouse pencil, which has no shades, fills with it too.
  - **Wet strokes** (`ink-live`, [collab.md](collab.md) "presence";
    `ink/inkLive.js`, pure, both halves). A stroke's id is minted at the
    down (`strokeId`), and the committed stroke is encoded under it. The
    layer sends the stroke through `onInkLive(page, msg)`: at most every
    50 ms while drawing (`LIVE_MS`, a trailing timer), the samples since
    the last chunk (`liveSamples`: `[x, y, p, t, a, z]`, page points to two
    decimals, `p` 0..1 or null without pressure, `t` ms since the stroke's
    first sample, integer degrees or null, at most `LIVE_SAMPLES` 64 a
    chunk) with `n` the index of the chunk's first sample and the look
    the stroke commits with (`liveStyle`); on the lift, once the stroke is
    committed, the rest, the last chunk (possibly empty) marked `done`;
    `drop: true` when it ends uncommitted (a cancel, a scribble that
    erased, the paste hold, a lost pointer). A committed stroke an edit
    takes off every group before a saved file holds it (an undo, an
    erasure, a cut, a delete within the 700 ms before the first flush)
    gets a `drop` too, from App: its `liveLedger` (`inkLive.js`) keeps the
    ids sent live per surface until a save holds them (`saved`, after
    `markSaved`), and `applyInk` asks it which of an edit's strokes left
    every group (`removed`; a stroke moved to another group stays), since
    no file would ever land them on the peers' pages. A host-claimed iPad
    stroke sends nothing: peers see it when its file lands. The peers' chunks
    land in `inkLive`'s store (`receiveInkLive`: a first chunk starts a
    wet stroke, a chunk whose `n` is the count held adds to it, any other
    drops it — a missed chunk draws nothing more until the committed
    stroke lands), by surface, keyed `client/id`. Not React state: each
    layer subscribes to its own surface (`useWetStrokes`) and paints the
    wet strokes on a canvas of their own (`.inkWetCanvas`, under the live
    canvas, cleared whole per paint) through `paintLiveStroke` with the
    style's width law baked on the fly (start taper only until `done`), so
    a peer's 20 chunks a second repaint one canvas and re-render nothing.
    A wet stroke goes when a group drawn on its surface holds its id (the
    layer's `landWet`, checked on every paint and render: the file landed,
    and the committed stroke stands where the wet one was; `g` names the
    group when the writer has one, but every group is checked), on `drop`,
    when its writer leaves the page (App's `keepWetOf(peers)`), with the
    page (`clearWet`), and 60 s after its last chunk (`WET_MS`). The
    canvas's `data-wet` counts the wet strokes drawn and `data-wet-ids`
    names them, for the e2e. A read-only surface without ink mounts its
    layer while it has wet strokes (`useHasWet`, `MarkupLayers.jsx`).
  - **Dense pages** (`useVisibleGroups`, `useIdleOutlines`,
    `useGroupRasters`; none of it changes a pixel of what is drawn once
    the outlines are built, and a tap's selection, the lasso and the
    eraser keep reading the samples; only the picture lets presses
    through, below).
    **Culling**: on a surface holding more than `IDLE_STROKES` (200)
    strokes, a group whose `inkBounds` box lies outside the part of the
    page in its scroller (`viewportOf`), plus half that scroller on every
    side, renders its `<g>` empty; the layer measures it itself, before
    the paint of every render and on every scroll frame, and an
    `IntersectionObserver` on the page (root the scroller, margin 50 %)
    tells when the whole page is that far off. **Idle outlines**: a group
    of more than `IDLE_STROKES` strokes with more than a slice (50) of
    them not built yet draws those as `strokePlaceholder` polylines and
    builds the outlines in `requestIdleCallback` slices of 50 (`setTimeout`
    0 without it), each slice swapped in as it lands, for the groups on
    screen; a stroke or two not built yet (a new stroke, a reload's
    additions) is built at once, so a lifted stroke never shows thin.
    **The raster**: on a surface holding more than `RASTER_STROKES`
    (1 500) strokes, every finished group's `<g>` (one per band) — not the
    group the layer's last stroke went into, nor one with a selection or
    in a replay, nor one culled — is drawn, once it has stood unchanged
    for a second, as a picture of itself: its own markup, with the layer's
    `<defs>`, as an SVG image drawn to a canvas at the current zoom in
    device pixels and kept as a PNG, shown as one `<image class="inkRaster">`
    in its `<g>` (multiplied onto the page for band 0) until the group
    changes. The picture takes no presses (`pointer-events: none`): an
    image is hit over its whole box, where the paths are hit on their ink
    alone, so a group's box would hide the page's text and links from a
    press in Hand mode; a tap on the ink still selects it, from the
    samples (`nearestInkStroke`), and a press on the ink itself reaches
    the page as one beside it would. A zoom draws the picture again from
    the markup it kept, the old one standing meanwhile; a group too large
    for a canvas (`canvasSize`'s caps) keeps its paths, its picture at
    the old zoom dropped. A layer's pictures hold at most `RASTER_PIXELS`
    (16 Mi device pixels, 64 MB decoded) together: one that would pass it
    drops the pictures of groups scrolled away first, else keeps its
    paths at that zoom. A browser that taints the canvas with the SVG
    image shows the SVG image itself.
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
    for a pen the **Style picker** (`InkStylePicker` in
    `ink/InkStylePicker.jsx`, since the selection menu uses it too and
    imports never run from `ink/` to `markup/`: a `ctlBtn inkStyleBtn` with `aria-haspopup="menu"`,
    `aria-expanded`, aria-label "Style: {brush}" naming the current one
    ("Style: Pen"), title "Pen style · Shift+[ ]", holding a
    `BrushPreview` glyph — a 28 × 10 SVG stroke drawn in that brush's
    look —, the brush's name and a `ChevronDownIcon`; its `onPointerDown`
    opens the hoisted `menu` state as `{kind: 'style', id}`, rendered
    after the `InkTooltips` element like the others, `InkStyleMenu`: a
    `ContextMenu` holding a `role="menu"` "Pen style" of
    four `MenuItem`s in the order Pen · Ballpoint · Brush · Pencil, each
    `role="menuitemradio"` with `aria-checked` on the current one and the
    `BrushPreview` as its icon, titled "Pen: width follows stylus
    pressure", "Ballpoint: an even line at every pressure", "Brush: a
    felt tip that swells with pressure and tapers at the ends", "Pencil:
    tilt widens the line, pressure darkens it"; a row calls `edit({brush})`
    with `'pen'` (which `normalizeTools` drops), `'monoline'`, `'brush'`
    or `'pencil'`), a highlighter's *Straight* trio in its place, the
    **Line** trio (`InkLineChoices`, `role="group"`, aria-label "Line":
    *Solid* · *Dashed* · *Dotted* pills, each with a glyph of its line,
    titled "Solid line", "Dashed line: dashes
    two widths long", "Dotted line: a dot every two widths", calling
    `edit({dash})` with `null`, `'dashed'`, `'dotted'`; pens and
    highlighters both) and the *Smoothing* five-dot ladder, `inkSizeBtn`
    dots for `stabilize` 0–4 —, Color — the `ColorChoices` —
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
    surface gets no edit callbacks, and one with no ink mounts no layer
    unless peers are writing on it (`useHasWet`, "Wet strokes").
    While the Text tool is armed the layer only draws.
  - `useMarks` (same file) builds App's marks per surface: its ink groups,
    and the lasso selection and the flash when they are on it. A surface's
    slice is kept while its parts are unchanged, so a memoized page
    re-renders only for its own ink.
  - `PageTools.jsx` `PageToolsContext` carries the tools to every layer:
    `{readOnly, ink: {tool, penTool, penOnly, pressure, eraserMode,
    eraserSize, eraserReturn, eraserFilter, drawHold, scribble,
    fingerUndo, lassoMode, hoverPreview}, text, actions}` —
    `eraserReturn`, `drawHold` and `scribble` the account flags,
    `eraserFilter` `'all' | 'pen' | 'highlighter'`, `fingerUndo` `'off' |
    'tap' | 'double'`, `hoverPreview` this browser's flag. The actions
    are the stroke, erase (`onEraseAlong(page, path, r, {mode, pass})`,
    the eraser's path since its last flush, "Erasing" above), select,
    action, move, jump and finger-scroll
    handlers (and the text boxes') plus `onEraseEnd()` (the tool eraser
    lifted), `onEraseMany(page, [{gid, ids}])` (a scribble's targets, one
    entry across groups), `onUndoGesture('undo' | 'redo')`,
    `onHoldStatus({text, once})`, `onPaste(page, pt, {width, height})`
    (a held pen or mouse, or a right-click, asking for the clipboard at a
    page point, with the surface's size in points), `onHaptic(kind, at)`
    (a snap or a pen lasso's selection, for the iPad host's haptics) and
    `onInkLive(page, msg)` (a chunk of the stroke in progress for the
    page socket, "Wet strokes" above),
    ref-backed through `useStableActions`, so their identity never changes.
    The peers' strokes in progress are not marks: a layer reads them from
    `ink/inkLive.js` for its own surface ("Wet strokes" above).
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
  `stabilize` 0–4 as an integer, omitted at 0, a pen's `brush` among
  `monoline`, `brush`, `pencil` (`KNOWN_BRUSHES`; `'pen'`, an unknown
  value or any brush on a highlighter is dropped, so the plain pen is the
  absent key), a `dash` of `dashed` | `dotted` on a pen or a highlighter,
  omitted when solid, a highlighter's `straight`
  `'always' | 'off'`, omitted at `'hold'` and dropped from a pen, a
  `hidden: true` flag, omitted when false, and any other key a preset
  carries, verbatim, so an older build that edits one preset never strips
  what a newer one wrote — the known keys first and the rest only while
  the preset serialises under 512 bytes of UTF-8, and a list that hides every
  preset shows its first, so the row is never empty; `toolStyle` copies
  `brush` and `dash` onto the style a stroke is encoded with and carries
  smoothing and straightening onto the armed tool for the layer, never
  onto a stroke; `inkToolName(preset)` reads "{color} pen", "{color}
  ballpoint pen", "{color} brush pen", "{color} pencil" and "{color}
  highlighter" (the line is not named), and `visibleTools(list)` is the list without the hidden
  ones, what the strip, the key table and the stylus's `backTool` read; the
  eraser's mode and size; the lasso mode; the account's `inkDrawHold`,
  `inkScribbleErase`, `inkEraserReturn` and `inkEraserFilter` and this
  browser's `inkFingerUndo`, `inkHoverPreview`, `inkPencilTap`,
  `inkPencilSqueeze` and `inkHaptics`, [settings.md](settings.md)
  "Reading & editing"),
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
  The flush starts every dirty group's requests at once
  (`ink/inkFlush.js` `planFlush` says what each sends and which go with
  `keepalive`), uploading each draft (`POST /api/upload-ink`, the
  `serializeInk` bytes) and PATCHing its block through `PUT /api/blocks/{id}` with
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
    tool only under `'all'`). App applies it in `handleInkEraseAlong`
    and `handleInkEraseMany` — `hitStrokesAlong(ink, path, radius, keep)`
    and `eraseAlong(ink, path, radius, keep)` take it as an optional last
    argument and leave a stroke `keep` refuses untouched, the same object,
    so the caches survive — and the layer builds it from
    `ink.eraserFilter` for the scribble stage and the hover glyph.
  - `handleInkEraseAlong(page, path, r, {mode, pass})`, the eraser's
    flush: `eraseAlong` (partial) or `hitStrokesAlong` and `removeStrokes`
    (whole strokes) over every group of the surface (`inkBySurface`)
    through the filter, the changes of all of them one `applyInk`. Every
    flush of one pass folds into one history entry, `T("partial ink
    erasure")` or `T("ink erasure")`, keyed by the pass (`applyInk`'s
    `pass` option keeps it on the entry; `applyInk` answers whether the
    edit applied, since the byte budget may refuse it): a group the entry
    holds takes the flush's `after`, another joins it with its `{id,
    page, before, after}`, so one Ctrl+Z undoes the pass across groups. A
    group whose drawing is no longer the entry's `after` (a save that
    merged someone's strokes in meanwhile) starts a new entry instead, so
    undoing the pass never takes their strokes away.
  - `inkBack()` is the previous distinct tool, else the stylus pen
    (`pencilActions.js` `backTool` over `inkPrevRef`); `onEraseEnd` with
    `inkEraserReturn` on and the eraser armed picks it with the options
    row closed, except while a held squeeze is open (`squeezeRef`'s
    `hold`: its release goes back). A squeeze that toggles the eraser
    still toggles it; with the return on, the lift re-arms the pen and the
    next squeeze arms the eraser again.
  - **The Pencil's gestures** (`pencilRef`, listening to the host's
    `pencil` and `squeeze` under `nativeHost().pencil`, on a page that
    takes ink): each goes through `pencilAction(message, {tools, pen,
    tap: inkPencilTap, squeeze: inkPencilSqueeze}, {tool, previous,
    options, busy: inkBusy(), squeeze: squeezeRef.current, now})`.
    `{pick}` runs `pickInkTool`, `{ui}` patches `inkUi`, `{undo}` runs
    `inkUndo(false)` (the strip's Undo: the stroke history, or the block
    history under `boxUndo`), `{began}` opens `squeezeRef` (`{since,
    ignored, hold}`; a `pick` is armed, with the tool before and the
    lasso's selection kept in `hold`), `{release}` picks back (the
    selection restored when that is the lasso, as `selectionStill(selection)`
    has it: without the strokes erased meanwhile, none when nothing is
    left; `stay` keeps the tool the squeeze armed), and an `ended` or
    `cancelled` closes `squeezeRef` whatever the answer. An erase pass
    (`handleInkEraseEnd`) or a committed stroke while a hold is open
    marks it `used`. A host `squeeze` is taken only in its `began` and
    `cancelled` phases.
  - **Haptics** (`hostHaptic(kind, at)`, the page tools' `onHaptic`):
    `postToHost({type: "haptic", kind, at?})` when `nativeHost().haptics`
    and this browser's `inkHaptics` are on — `tool` for a gesture's pick
    that changed the armed tool (an `{ui}` or an `{undo}` plays nothing),
    and, from the layer, `select` for a pen lasso that selected (at its
    lift) and `snap` for a held shape (at the tip), both in viewport CSS
    px.
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
  - The selection's `style` action: `handleInkAction(page, "style",
    value)` routes `value.brush` (present, `null` for the plain pen) to
    `restyleBrush` with the entry `T("ink style change")`, `value.dash`
    (present, `null` for solid) to `restyleStrokes` with `T("ink line
    change")`, and colour and size as before; each is one entry and goes
    through `applyInk`'s byte budget like any edit.
  - **Live co-drawing**: `onInkLive(page, msg)` (`handleInkLive`) adds
    the surface as `page` and `g`, the group the stroke joins when that
    group's block has a file (null before its first flush; worked out at
    the stroke's first chunk), and hands the chunk to the page's collab
    session, `sendInkLive(msg)`, which writes `{t: "ink-live", ...msg}` on
    the socket when it is open and drops it otherwise (never queued, never
    retried: the committed stroke follows). Incoming `ink-live` messages
    (the session's `onInkLive`) go to `inkLive.receiveInkLive`, the wet
    strokes' store ("Wet strokes" above), not App state; an effect on the
    session's `peers` keeps only the wet strokes of peers still on the
    page (`keepWetOf`: a `leave`, or the socket closing, which empties the
    peers), and leaving the page clears them (`clearWet`). Only a peer who
    `can_edit` sends (a read-only page has no edit actions, and the server
    drops a viewer's); a viewer only receives.
  - **The journal pill**: `inkStore.onInkRestored` raises "Unsaved
    handwriting from a previous visit was restored." with `{label:
    t("Discard"), run}`, once per block (a block is restored at most once
    a visit), and schedules the flush that saves the restored draft.
  - **Hold-E**: the strip handler takes `stripKey`'s `eraser` action —
    `down` arms the eraser as a tap does and keeps `inkHoldRef = {since,
    erased: false, selection, tool}` (the lasso selection and the tool
    armed before); `onEraseEnd` marks `erased`; `up` with `held ≥ 300`
    and `erased` picks `tool` again and, when that is the lasso, restores
    what is left of the snapshot (`selectionStill`); a shorter press, or
    one that erased nothing, leaves the eraser armed; a window `blur`
    clears the hold.
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
  `stroke_width` (`size × w`, else `size`: no law) and `stroke_opacity`
  (`opacity × o`, else `opacity`), `stroke_polyline`
  (variable-width polylines every renderer draws from, band 0 first),
  `DASH` and `dash_pattern(stroke) → (on, off) | None` (the client's
  table), `dash_runs(stroke)` (the on-runs by arc length, boundary
  samples lerped in x, y, p, t, w and o, a and z from the sample before;
  `tests/shared/inkdash.json` pins both against the client's
  `dashArray` / `dashRuns`, and with them the run splitting the bands
  share), `opacity_band(o)` and `stroke_bands(stroke)` (the stroke cut
  into runs of one opacity band, `o` quantised up to 0.33 / 0.66 / 1, a
  sample lerped where `o` crosses an edge — the pencil's shading; one run
  at band 1 for a stroke without `o`), `stroke_outline(stroke, run?,
  caps?)` (perfect-freehand 1.2.2's outline at the client's `outlineOptions`,
  ported line for line — a test pins it to the library's output on a
  stroke that turns back on itself — and driven by pressure `w / 2`, so
  its half-width is `size × w / 2`; it runs at ten times the page's
  scale, since perfect-freehand drops every point within 3 units of a
  path's end and a dash or band run shorter than 3 pt would outline as a
  blot, and a run of two samples gets a midpoint first, since its
  two-point path reads their pressure as 0.5), `bounding_box` (over the
  raw integer arrays, no per-sample objects: the running sums of x, y and
  w, which `routers/ink.py` computes on every flush) / `pdf_position`
  (the shape is `gamma/highlights.py`'s). The exporters draw a stroke one of
  four ways (`_drawn_as`): a **highlighter** as its centreline at its
  size, multiplied onto what lies beneath; a **dashed** stroke as one
  centreline at its mean drawn width and mean drawn opacity, round caps,
  the pattern its size's; a **pencil or a translucent pen** (`o`, or an
  opacity under 1) as a filled outline per opacity band, each band one
  path painted once, so it never darkens where it overlaps itself, as
  stroked segments under an alpha would, and each run round only at the
  stroke's own ends and flat where it meets the next (the client's
  `runOutline`), so two bands meet without overlapping; an **opaque solid
  pen** (and any tool this code does not know) as one
  round-capped segment per sample pair at that pair's baked width.
  `to_svg` writes them as a `<path>` with `stroke-opacity` and
  `mix-blend-mode: multiply`, a `<path>` with `stroke-dasharray="on off"`,
  a `<path>` per band with `fill-opacity` band × opacity (untextured), and
  `<g>` of segments; `pdf_path_ops(ink, to_pdf, scale, band?)` (the
  notes-as-PDF writer, the notebook PDF and the AI's page picture; `band`
  keeps one band, for a writer drawing several groups band by band; `b""`
  when nothing is left) writes the highlighter under `/GSinkM<α × 1000>`
  (`/CA`, `/ca` and `/BM /Multiply`), the dash between `[on off] 0 d` and
  `[] 0 d` (scaled with the drawing), each band as cubic outlines filled
  once under `/GSink<α × 1000>` (`/CA`, `/ca`; none at 1), and the segments
  as before. A graphics state's name carries it, so `pdf_path_resources(ops)`
  declares every state some ops set from the ops alone (None when they
  set none); the writers embedding them put it in the page's `/ExtGState`.
  For the `/Ink` interchange, `ink_buckets` (key unchanged: tool, colour,
  size, opacity) and `from_pdf_ink` (a foreign `/Ink` whose `/BS` has
  `/S /D` imports as `dash: "dashed"`, whatever its `/D` array, since the
  pattern follows the width). `dumps` (the canonical bytes, equal to the
  client's `serializeInk`, so the same strokes dedup to one upload),
  `merge_ink` (→ the file, `clean`, `lost_ours`, `returned`). The server
  computes no width and no opacity: it reads `w` and `o`.
- `routers/collab.py`: the page socket relays a client's `{t: "ink-live",
  …}` to the room's other peers as it came, with `client` set to the
  sender's, when `collab.ink_live` admits it — the sender `can_edit`, `id`
  is a string, `samples` holds at most 64 entries and the relayed message
  is at most 16 KB ([collab.md](collab.md) "presence"); nothing is stored,
  logged or replayed to a later joiner (`backend/tests/test_collab.py`).
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
  `/BS /S /D /D [on off]` when the bucket's first stroke is dashed (a
  bucket mixing dashes writes that first one's; `/GammaInk` carries the
  truth, and the bucket key does not change, so re-exports keep their
  `/NM` set),
  the note on the first, an `/NM` Zotero needs to import it as ink (the
  Zotero export's PDF copies carry it too), and a private `/GammaInk` key
  carrying the bucket's v2 strokes verbatim so a Gamma re-import keeps
  pressure, time and every key; the embedded-annotation importer reads
  `/Ink` (Gamma's or anyone's) into ink blocks, converting a version 1
  `/GammaInk` payload, and strips them like the other types; the Markdown export writes
  `![Handwriting (p.N)](assets/<hash>.svg)` with the SVG generated into the
  zip; the notes-as-PDF document draws the strokes as vectors under a
  "handwriting, p. N" line, and the notebook PDF on their sheet, every
  group's band 0 before any group's band 1, each page declaring the
  graphics states its ink sets. The agent's `read_block` outline labels
  an ink block "handwriting on p. N, K strokes" before its caption.
- `gamma/ink_view.py`: the picture the chat sees of a group (`view_ink`,
  and an attached handwriting block), drawn where it was written. A sheet
  goes through `notebook_pdf`, a PDF page through
  `pdf_export.page_with_ink(src, page_no, inks, vector=True)`: every
  stroke is stamped through `pdf_path_ops` into the copied page's content
  (`_stamp_ink`: the page's own content wrapped in `q … Q`, the strokes
  appended after it, mapped by `page_frame` like the annotated export,
  the states they set added to the page's `/ExtGState`), every group's
  band 0 before any group's band 1. The pens show their baked widths,
  tapers, dashes and pencil bands; the highlighters are multiplied onto
  the page, so the text under them stays as dark as it was, and they stay
  under the ink, which `/Ink` annotations — drawn over the page's content —
  could not. `vector=False` is the old all-`/Ink` picture (nothing calls it
  but the tests). MuPDF rasterizes either ([ai_tools.md](ai_tools.md)
  "view_ink"). `backend/tests/test_ai_tools_ink.py` renders text under a
  highlighter and asserts every glyph pixel stays dark, a pen crossing a
  highlighter drawn after it and asserts the pen's pure colour, a pencil
  stroke and asserts its three shades, and a dashed one and counts its
  dashes.

## Not built yet

Dedicated shape tools (a shape comes only from draw-and-hold; a committed
shape has no handles yet, though its `shape` record is written for them),
the calligraphy pen
(the Style picker's fifth row; `nib` is stored and preserved but nothing
writes it, and the iPad shell's claim does not carry it), a fill on
closed shapes (`fill` likewise), the pencil's grain
in exports and the AI picture (they draw its shades as plain bands), the
pencil's grain and a dashed pressure line's widths on the iPad shell's
native live line (it draws the pencil untextured and a dashed line at an
even width until the web's stroke replaces it), live co-drawing of a stroke the iPad shell
captures (its samples reach the page only at the lift, so peers see it
when its file lands), reading handwriting back from the system
clipboard (copy writes a `gamma/ink` item for other apps, but a paste
reads only this tab's own clipboard, so nothing pastes across tabs yet),
Xournal++ `.xopp` import, and a
replay of erasures and edits (it shows the drawing as it is, in the order
it was written). Audio recording to go with the replay is
[recording.md](recording.md). Obsidian vault export writes an ink block's
caption only. The Notability comparison in the research note lists what a
closer pen experience still needs (a snap of
straight highlights to the PDF's text lines); draw-and-hold, smoothing,
the straight highlighter, scribble-to-erase, the eraser's return and
filter, finger undo, the highlighter
drawn behind the ink (`band`), the handwriting clipboard, flips, note
split and merge, presets on the account and their drag order, the Pen /
Ballpoint / Brush / Pencil styles, dashed and dotted lines, the
selection's Style row, live co-drawing over presence, the dense-page
floor, the draft journal, and in the iPad app draw-and-hold, hold to
paste, the preset's look on the live line, the Pencil's tilt, the
gesture choices and the haptics are built. The broader interaction survey is
[handwriting-interactions.md](../research/handwriting-interactions.md).
