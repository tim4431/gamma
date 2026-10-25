# Handwriting (ink) annotations

Draw on a PDF page with a stylus, mouse or finger, and the strokes become a
block in the page's notes. The survey behind the shape, and how Notability
does the same things, is in
[research/handwriting.md](../research/handwriting.md). What Notability and
GoodNotes store, and why the file looks the way it does, is in
[research/ink-storage.md](../research/ink-storage.md). The same files are
written on notebook sheets ([notebooks.md](notebooks.md)) and by the iPad
app ([ipad.md](ipad.md)). Typed text on a page is
[text_boxes.md](text_boxes.md).

- Server: `gamma/ink.py`, `gamma/routers/ink.py` and `gamma/ink_view.py`.
- Client, in `frontend/src/`:
  - `ink/ink.js`, `ink/finishStroke.js`, `ink/shapes.js`,
    `ink/shapeTool.js`, `ink/ruler.js`, `ink/zoomWindow.js`,
    `ink/surfaceScale.js`, `ink/gestures.js`, `ink/stabilizer.js`,
    `ink/fingerChords.js`, `ink/stripKeys.js`, `ink/pencilActions.js`,
    `ink/hostInk.js`, `ink/inkFlush.js`, `ink/inkLive.js`, `ink/grain.js`
    and `ink/inkInput.js`;
  - the drafts and the save, `ink/inkStore.js`, `ink/inkJournal.js` and
    `ink/inkSaver.js`;
  - the components `ink/InkStylePicker.jsx`, `ink/InkLayer.jsx` and
    `ink/ZoomWindow.jsx`;
  - the owners of the handwriting state App composes, `ink/buildFence.js`,
    `ink/useInkStrip.js`, `ink/useInkLive.js`, `ink/useInkSave.js`,
    `ink/useInkSurfaces.js`, `ink/useInkDocument.js`,
    `ink/useInkStrokes.js`, `ink/useInkSelection.js`, `ink/useInkRuler.js`,
    `ink/useInkZoom.js`, `ink/useStripKeys.js` and `ink/usePencil.js`;
  - the owners' pure parts, `ink/stripState.js`, `ink/inkSurfaces.js`,
    `ink/inkHistory.js`, `ink/inkEdits.js`, `ink/selectionEdits.js` and
    `ink/inkCommands.js`;
  - the tool strip and the page plumbing it shares with text boxes and
    pictures, `markup/MarkupToolbar.jsx`, `markup/MarkupLayers.jsx` and
    `markup/PageTools.jsx`, and the lasso's resize and rotate handles,
    which a picture shares, `markup/TransformHandles.jsx`.
- Tests: `backend/tests/test_ink.py`, and in `frontend/tests/`:
  - `ink.test.mjs`, `inkErase.test.mjs`, `finishStroke.test.mjs`,
    `shapes.test.mjs`, `shapeTool.test.mjs`, `ruler.test.mjs`,
    `zoomWindow.test.mjs`, `gestures.test.mjs`, `stabilizer.test.mjs`,
    `fingerChords.test.mjs`, `stripKeys.test.mjs`, `pencilActions.test.mjs`,
    `hostInk.test.mjs`, `inkInput.test.mjs`, `inkLive.test.mjs`,
    `inkJournal.test.mjs`, `inkFlush.test.mjs`, `inkSaver.test.mjs`,
    `inkRender.test.mjs` and `surfaceScale.test.mjs`;
  - the owners' pure parts: `buildFence.test.mjs`, `stripState.test.mjs`,
    `inkSurfaces.test.mjs`, `inkHistory.test.mjs`, `inkEdits.test.mjs`,
    `selectionEdits.test.mjs` and `inkCommands.test.mjs`;
  - the benchmarks `bench/inkCache.bench.mjs` and `bench/inkErase.bench.mjs`,
    which are not part of `npm test`.
- Shared fixtures in `tests/shared/` are read by
  `backend/tests/test_shared_fixtures.py` and the node tests:
  - `inkv2.json`, `inkwidth.json`, `inkdash.json`, `inkmerge.json`,
    `inkcodec.json`, `inkgrain.json` (the pencil's grain tile in each
    colour), `inknib.json` (the calligraphy nib's paths) and
    `inkoutline.json` (strokes the screen outlines whole). The last three
    run in `inkRender.test.mjs`.
  - `inkwidth.json` runs in the iPad app's `InkWidthTests` too.
  - The recognisers and the Shapes tool's drags (`inkshapes.json`,
    `inkscribble.json`) run in the node tests only, since the server never
    recognises. The Python side checks those fixtures' shape and that
    every stored `shape` record parses.
  - The samples a shape record is drawn as (`inkshapes.json` `samples`)
    and the sample counts of the drags run on both sides (`shapes.js`
    `shapeSamples`, `ink.py` `shape_samples`).
  - `inkhold.json`, the hold's stillness and refit rule, runs in the node
    tests and the iPad app's XCTest.
- e2e: `tests/e2e/scenarios/ink.mjs` (the draft journal's restore too),
  `inkEditing.mjs`, `inkShapes.mjs` (group `ink-shapes`: the Shapes tool,
  the ruler, the calligraphy pen, shape handles and fills), `inkZoom.mjs`
  (group `ink-zoom`: the zoom window and transcribing a selection), and the
  live stroke in `collab.mjs`. The scratch scenario `tests/e2e/tabClose.mjs`,
  apart from the groups, closes the tab as the pen lifts, also with no
  `pagehide` listener of the app running
  ([debugging.md](debugging.md#browser-end-to-end-suite)).

## What the user sees

- The pen button in the viewer's zoom column opens the **tool strip** at
  the top of the page, laid out like Notability's. Its accessible name is
  "Markup tools".
  - One tap arms a tool. **Tapping the armed tool again opens its options
    row** under the strip.
  - Opening the strip arms the last pen used, or the first pen on the
    strip when that one is hidden, even with a highlighter before it.
  - The first row is five groups, split by thin rules (`.pdfInkSep`):
    - the **tool presets**, each a pen or a highlighter with its own colour
      and width, shown as the icon over a colour bar (four pens and three
      highlighters to start);
    - the eraser, the lasso, the Text tool (typed text boxes,
      [text_boxes.md](text_boxes.md)) and the Picture button (a photo or an
      image placed on the page, [pictures.md](pictures.md): a button that
      opens the file picker, not a tool that stays armed, and none on a
      read-only page), then a pressed *Ruler*, *Shapes* and *Zoom window*
      button, each only while it is on (below);
    - a hand (nothing armed: scroll and select text);
    - **More** (⋯, "More handwriting tools") and close;
    - Undo / Redo, pinned at the right end as the last two buttons.
- **More** opens a menu on the press, which takes the button's tooltip
  away, so the menu takes the tooltip's place in the same frame. The menu
  is a `ContextMenu` of `MenuItem`s rendered outside the strip's tooltip
  host, so its rows raise none of the strip's tooltips.
  - Its rows:
    - *Paste handwriting* (Ctrl+V) and *Select all handwriting on this
      page* (Ctrl+A);
    - a divider, *Ruler* (R), *Stamp line*, *Shapes* (S) and *Zoom window*
      (Z);
    - a divider, and *Handwriting settings…* (Settings → Reading & editing,
      at the Handwriting section).
  - *Paste handwriting* is greyed, titled "Copy or cut handwriting first",
    until the handwriting clipboard holds strokes. *Stamp line* is greyed,
    titled "Show the ruler first", while the ruler is hidden.
  - *Ruler*, *Shapes* and *Zoom window* are each ticked while on.
  - *Zoom window* is greyed, titled "The zoom window works on PDF pages",
    while the viewer shows no PDF page (the notebook view, the PDF hidden,
    a page without one). It is left out on a phone.
  - The first two rows are the catalog commands `ink.paste` and
    `ink.selectAll` drawn as `CommandMenuItem`s, so a rebound chord shows
    there too. R, S and Z are the strip's own keys, named on their rows.
  - Escape or a press outside closes it. The button is
    `data-guide="ink.more"` for the guide.
- **A preset's options row** is three captioned sub-rows (the caption a
  14 px line in the small caption size).
  - On a phone (480 px of viewport width and under) the captions go, Style
    and Width share one wrapping line and Color takes the next. A narrow
    phone wraps the open row onto several lines.
  - On a phone the dot buttons (the widths, Smoothing, the eraser's sizes)
    narrow to 24 px and the pills' padding to 6 px.
  - **Style**, for a pen, is the **Style picker**: a button showing a
    short stroke drawn in the current style, its name and a ▾ (*Pen ▾*).
    The preset reads "Red ballpoint pen · 4 pt · key 1", "Blue brush pen ·
    2 pt", "Black pencil · 2 pt", "Black calligraphy pen · 2 pt".
    - It opens a menu on the press, like More, whose five rows are each led
      by the same stroke preview, the current one ticked:
      - *Pen*: width follows the stylus pressure.
      - *Ballpoint*: an even line at every pressure. The stored brush is
        `monoline`, a word that never shows.
      - *Brush*: a felt tip, twice as wide at full pressure and thin at
        none, tapering in at the start and out at the end. With a mouse or
        finger it swells where the pointer moves slowly and thins where it
        moves fast, so it is a felt look without a stylus.
      - *Pencil*: graphite, the line darkening with pressure and widening
        as the pencil leans, with a grain on the screen. A mouse draws a
        soft even line.
      - *Calligraphy*: a flat nib, thick where the pen moves across the nib
        and thin where it moves along it ("Calligraphy" below). Its Style
        row adds the **Nib angle** after the picker.
    - `Shift+[` / `Shift+]` step the armed pen through the five, in that
      order.
    - On a highlighter the *Straight* pills (Hold · Always · Off, its
      `straight`, "Straight highlighter" below) stand where the Style
      picker would.
  - The Style row goes on with the **Line** trio, *Solid* · *Dashed* ·
    *Dotted*, for pens and highlighters alike, and the *Smoothing* ladder
    of five dots (Off, 1–4: the preset's `stabilize`, "Smoothing" below).
    Dashes are two widths long with two-width gaps and dots sit two widths
    apart, so a thicker pen has longer dashes.
  - **Color** is the palette, the colour groups the account chose for pen
    presets or for highlighter presets (Settings → Appearance › Colors,
    [settings.md](settings.md)), plus a custom colour through the
    browser's picker. By default these are the 14 dark "Pen" colours, and
    the eight light "Ink highlight" colours (the four text highlight
    colours and four more).
  - **Width** is eight widths as dots, titled "Width 4 pt · [ ]" (the
    bracket keys step them). Then come *Duplicate* (a copy right after it,
    armed and still open for editing), *Remove* and a ⋯ button, the **Tool
    menu**.
  - The **Tool menu** opens on the press like More.
    - Its rows are *Move left* and *Move right* (greyed at the row's ends;
      the pill says "Blue pen is now key 3."), *Duplicate*, *Hide*,
      *Hidden tools ▸*, *Remove*, a divider, and *Reset this tool*. *Reset
      this tool* sets colour, width and style back to the default pen or
      highlighter of its kind.
    - *Hidden tools* is a submenu naming each hidden preset ("Blue pen · 2
      pt"), where a tap brings it back. With none it is greyed, "No hidden
      tools".
    - *Remove* and *Hide* (the button and the menu rows) are greyed at the
      last visible preset, whether or not hidden ones remain: the row never
      empties.
  - Every change applies to that preset, so the row is the user's own set
    of pens (up to 12, the account's `inkTools`, "Presets follow the
    account" below).
    - Each keeps the concrete hex it was given and, when a swatch brought
      one, its own `opacity` (the swatch's alpha). A preset without one
      draws at the kind's usual opacity, 0.6 for a highlighter and 1 for a
      pen (`toolStyle`).
  - Colours are named in words, never hex codes: a preset reads "Pink
    highlighter · 14 pt · key 7", and each swatch is titled and labelled
    by its name. Any colour, a custom one or a changed swatch included,
    takes the name of the nearest reference colour (`colorName` in
    `shared/model/palette.js`, redmean distance).
- **Reorder by dragging.** A preset button follows a mouse drag of 6 px, or
  a finger or pen held 500 ms and then moved, along the row.
  - A ghost of the button rides with the pointer, a 2 px accent bar shows
    where it will land, and the pill says "Drop to reorder."
  - The drop writes the new order (ids unchanged) and the number keys
    follow it. Dropping outside the strip, or Escape, leaves the order as
    it was.
  - A hold with no movement is a plain tap that arms the tool.
- **Hide** takes a preset off the row, out of the `1`–`9` / `P` / `H`
  cycles and away from the stylus, which then writes with the next visible
  pen.
  - The pill says "Blue pen hidden." with *Show*. *Hidden tools* in any
    preset's ⋯ menu brings it back where it was.
  - Keys `1`–`9` always count the visible presets in row order.
- **Presets follow the account.** `inkTools` lives in the profile
  ([settings.md](settings.md) "Reading & editing"): a browser that signs
  in takes the account's list, and an account without one is seeded from
  the browser.
  - When a load's first pull puts the account's list in place of a list
    this browser changed itself, one pill asks "Use this browser's pens
    instead?". Its *Use this browser's* pushes the local list, and
    otherwise the account's pens stand.
  - A list this browser changed itself is neither the defaults nor the
    list it last took from an account or sent there. The browser remembers
    that list whichever account it was, so the next account to sign in is
    not asked about the last one's pens.
  - A later pull that differs, and a later load that finds the list this
    browser last took or sent replaced, bring another device's edit, which
    applies without a question.
  - The question stays on the pill for its 8 seconds, or until it is
    answered. A page opening under it (a `?page=` link, or one to a page
    that is gone) shows its own messages over it for their second, and
    then the question again.
  - Every write of the list goes through `normalizeTools` (`setInkTools`
    in `ink/useInkStrip.js`), so what this browser keeps and pushes is
    exactly what a pull reads back.
  - Which pen a stylus writes with stays session state. The eraser's mode
    and size and the lasso mode stay this browser's.
- **Keys while the strip is open** (`ink/stripKeys.js`) arm the presets
  and tools and step the armed preset's width and style. The full list is
  [hotkeys.md](hotkeys.md#widget-keys-the-markup-strip-and-a-text-box)
  "Widget keys".
  - R, S and Z turn the ruler, the Shapes tool and the zoom window on and
    off, and Esc closes one layer at a time.
  - Ctrl+C / X / V / D / A are catalog commands instead ("Copy, cut and
    paste" below), so Settings → Keyboard lists and rebinds them.
- The **eraser**'s options row: *whole strokes* removes anything it
  touches, *partial* cuts through them (the pieces on either side become
  their own strokes), three sizes, then *Erases* and *Back to last tool*.
  - *Erases* (All · Pens · Highlighters, the account's `inkEraserFilter`)
    is what any erasure may remove: the eraser tool, the pen's eraser end
    and barrel button, a scribble. A stroke of an unknown tool goes only
    under All.
  - With a filter on, the hover ring carries a small pen or highlighter
    glyph.
  - *Back to last tool* (`inkEraserReturn`, off by default): lifting the
    **tool** eraser returns to the tool armed before it with the options
    row closed, whether or not the pass erased anything. The pen's eraser
    end and barrel never switch tools, since lifting them is the switch,
    and a gesture ended by `pointercancel` does not return.
  - The partial eraser cuts exactly at its edge along the whole path it
    swept, however fast it moves. A scrap shorter than half the line's
    width goes with the rest.
  - A stroke it crossed between two of the positions the pointer
    reported, or between two of the stroke's own samples (a fast mouse
    stroke, a held straight line of two samples), is cut there too. Each
    piece ends where the eraser's edge met the ink.
  - A pass, from the press to the lift, is one undo entry even across
    groups, whichever mode erased.
- The **lasso**'s row: *freeform* circles strokes (more than half their
  samples inside) and *box* drags a rectangle. The dashed box then moves
  by dragging and deletes with `Delete`, and both work across groups on
  the page.
- **Draw and hold** (`inkDrawHold`, the account's, on by default): draw a
  line or a shape and keep the pen still at its end. After 600 ms the
  stroke snaps to the recognised shape and flashes in the selection blue
  for two frames.
  - The shapes:
    - a line, snapped to 0°, 45° or 90° when within 5°;
    - an arrow, a line with a short symmetric head;
    - a triangle;
    - a rectangle, axis-snapped within 5°, and a square when the sides are
      within 10 %;
    - a regular polygon of 5 to 8 sides, only convex and closed (a zigzag
      never becomes a hexagon);
    - a circle and an ellipse.
  - The hold counts only when the stroke's extent, its bounding-box
    diagonal, is at least max(24 css px, 18 pt). So a shape closed where
    it began snaps like any other, and zoom cannot turn a small letter
    into a shape.
  - The stroke must also have lasted 250 ms or more before the tip went
    still.
  - The timer restarts whenever the tip moves more than 3 css px, so a
    pause mid-letter needs a real stop.
  - Keep holding and move 6 css px or more to refit: a line follows the
    tip as its new endpoint, and a closed shape scales about its centre
    and rotates with the tip. Shift snaps the rotation to 15°, and a
    rectangle or triangle keeps its proportions.
  - Moving more than 12 css px within 150 ms of the snap cancels it and
    the freehand stroke goes on. Lifting before the timer fires commits
    the freehand stroke.
  - With a mouse, Shift while drawing straightens at once. It is part of
    Draw and hold, so off with it.
  - A highlighter snaps to lines only.
  - The committed stroke is an even line (`pen: false`, no pressure) in
    the preset's colour, width, style and line, at that width end to end
    whatever the style.
  - A shape is constant width, so a Brush preset's has no tapers and a
    Pencil preset's no shading (its grain stays). The snapped shape under
    the pen is drawn the same way.
  - Its `t` is spread over the time the stroke took, and it carries a
    `shape` record ("The stroke file"). The pill says "Straightened." or
    "Shape: circle." with Undo one tap away, every time.
  - In the iPad app the shell owns the pen, so it tells the page when the
    Pencil has held still (`hold`, [ipad.md](ipad.md) "The pen"). The page
    recognises the shape then, the shell draws it under the pen and refits
    it by the same rules, and the lift commits it as here.
  - Under a shell that does not send `hold` nothing is recognised: a shape
    is never recognised at commit time without the live preview.
- **Shapes** (More → *Shapes*, or `S`, which arm it with its options row
  open): a drag on the page draws a line, an arrow, a rectangle or an
  ellipse from where it starts to where it ends.
  - The shape follows the pointer while dragging and is committed at the
    lift, one undo entry. A drag under 4 px draws nothing.
  - Shift turns a line or an arrow in 15° steps about its start, and makes
    the rectangle a square and the ellipse a circle.
  - The options row: *Line* · *Arrow* · *Rectangle* · *Ellipse* (the
    account's `inkShapeKind`, Line to start), what it draws with ("Uses:
    Blue pen · 2 pt") and *Fill*.
  - A shape takes the colour, width, opacity and line of the pen a stylus
    writes with, as an even line whatever that pen's style (a shape is a
    Ballpoint).
  - *Fill* (the account's `inkShapeFill`, off to start) gives its
    rectangles and ellipses a light tint of the pen's colour inside
    ("Fill" below).
  - The cursor is a crosshair. A finger draws shapes only where fingers
    draw (*Draws with: Pen and finger*), and a pen and the mouse always do.
  - While it is armed the strip shows a pressed *Shapes* button after
    Text, whose tap opens and closes the row. S, the More row and Esc give
    back the tool armed before it.
  - Its strokes carry their `shape` record and edit like a held shape
    ("Editable shapes" below).
  - In the iPad app the page draws them as in Safari: the shell captures
    only pen and highlighter strokes.
- **Ruler** (More → *Ruler*, or `R`, which show it with its options row
  open): a straight edge laid over the page or sheet in view, in the
  selection blue. It is a band 36 px wide with ticks along it, both edges
  drawn as lines, a compass knob in the middle showing its angle, and a
  handle at each end.
  - It keeps its size on the screen at any zoom, lies on the page and
    scrolls with it, and is not saved. It starts level across the middle
    of what shows of the page, four fifths of that wide.
  - A **pen** on the band, or any stroke begun within 12 px outside
    either edge (a pen's, the mouse's, a finger's where fingers draw), is
    drawn along the nearer edge. Whatever the hand does, the stroke is the
    straight line between the farthest points it reached along that edge:
    an even line like a held one (no tapers, no shading) in the preset's
    colour, width, style and line.
  - A **finger or the mouse** on the band moves the ruler. A second finger
    turns it with the first, and the page neither scrolls nor zooms
    meanwhile.
  - Dragging the knob turns the ruler freely (it follows the pointer round
    its middle, from where the pointer first stands 12 px out), or in 15°
    steps with Shift or with *Snap 15°* chosen in its row.
  - A tap on the knob snaps it to the nearest 15° and a double tap turns
    it 90°. Dragging an end handle lengthens or shortens it, the other end
    staying.
  - A focused band, knob or handle takes the arrow keys:
    - the band moves 1 pt (Shift 10 pt);
    - the knob turns 1° (Shift to the next 15°, and with *Snap 15°* every
      press is a 15° step);
    - an end moves 10 pt out or in.
  - While it shows, the strip has a pressed *Ruler* button after Text,
    whose tap opens and closes the ruler's options row. The row holds
    *Angle 30°*, *Snap 15°* · *Free* (Free to start; choosing *Snap 15°*
    puts the ruler on the nearest 15° at once), *Stamp line*, *pt* · *cm*
    and *Hide*.
  - *Stamp line* draws a line along the middle of the ruler, end to end,
    with the armed pen or highlighter (the pen a stylus writes with when
    another tool is armed): one undo entry, "Line stamped along the
    ruler." with Undo. More has it too.
  - *pt* · *cm* (this device's `inkRulerUnits`) sets the ticks: a short
    one every 10 pt, a longer one every 50 and a number every 100, or
    millimetres with a longer tick every 5 and each centimetre numbered.
  - R, the More row, *Hide* and Esc take it away, and so do closing the
    strip and leaving the page. So does the viewer no longer showing its
    surface: a PDF page's when the PDF is hidden or the notebook view
    takes its place, a sheet's when the notebook view closes.
  - The eraser, the lasso and the Shapes tool ignore it, and on the band
    every pointer moves it then.
  - While the Text tool is armed the ruler still moves, and a press on it
    is never a new text box.
  - In the iPad app the shell draws the line along the edge under the
    Pencil too ([ipad.md](ipad.md) "The pen").
- **Zoom window** (More → *Zoom window*, or `Z`; PDF pages only, and not
  on a phone): a panel docked along the bottom of the viewer, the pages
  scrolling above it. It shows a strip of a page two, three or four times
  as large as at 100 %, so small handwriting is written large.
  - The panel shows the page and its handwriting, not its text boxes. The
    page shows the part it holds as a blue box.
  - What is written in the panel lands on the page, in the page's current
    group, as if written there: saved, undone, replayed and shared the
    same way.
  - Fingers in the panel neither scroll nor zoom. A pen writes there as on
    the page, palms refused, in the iPad app too.
  - Whenever a stroke reaches into the panel's right fifth, the window
    moves on along the line by three fifths of its width. When that would
    carry it past the page's right edge it goes back to where the line
    began, one line down.
  - Its bar:
    - *Previous line* and *Next line* (the window up or down one line);
    - *New line* (back to where the line began, one line down; Enter);
    - the line height (− 24 pt +, from 8 to 96 pt);
    - *2×* · *3×* · *4×* (3× to start);
    - *Close* (or Z, or Esc).
  - The panel is 160 to 320 px tall, 200 to start (this device's
    `inkZoomHeight`).
  - Its top edge drags to resize it: the pages above give up the space as
    it moves, and the height stays when the edge is let go. Focused, the
    edge takes ↑ / ↓, 10 px a press.
  - On the page, the box's top band drags it and its corner resizes it
    (the magnification follows, from 2× to 4×; the panel moves when the
    box is let go). Focused, the arrow keys move the band and step the
    corner, at once.
  - A tap on a blank spot of a page centres the box there, on another page
    too: a finger's tap, or the mouse's with the hand or the lasso, that
    does not just dismiss a selection. Each of these sets where its line
    begins.
  - The viewer scrolls to keep the box in view as it moves, and its own
    buttons at the bottom stand above the panel.
  - The panel draws the selection's dashed box, which a finger or the
    mouse drags, while the selection's menu and handles stay on the page.
  - While it is open the strip shows a pressed *Zoom window* button after
    Text, whose tap closes it. Closing the strip and leaving the page
    close it too.
- **Smoothing** (a preset's `stabilize`, 0–4, Off by default): the pen
  tip pulls the line behind it on a string of 3, 6, 10 or 16 css px, so a
  shaky hand draws a calm line. It is the lazy brush of Krita and
  Xournal++.
  - Pressure and time are untouched, a sharp reversal (over 90°) still
    makes a corner, and the lift catches the line up to the tip.
  - It applies while drawing only: saved ink is never re-smoothed, and the
    live line is the committed line.
- **Straight highlighter** (a highlighter preset's `straight`, Hold by
  default): *Hold* straightens through draw-and-hold, and *Off* never
  straightens.
  - *Always* replaces every highlight by its chord on lift when the whole
    stroke stays within min(0.75 × width, 6 pt) of that chord and the
    chord is at least four widths long.
  - The chord snaps horizontal within 4° and otherwise keeps its angle.
    The first *Always* straightening of a session says so once on the
    pill.
  - There is no snap to the PDF's text lines.
- **Scribble to erase** (`inkScribbleErase`, the account's, off by
  default): a fast dense back-and-forth with a pen erases the pen strokes
  under it as one undo entry, and the pill says "Erased 3 strokes." with
  Undo. The scribble takes at least four sharp reversals along its own
  long axis within two seconds.
  - Turning it on shows a pill explaining the gesture.
  - It takes the strokes at least 70 % inside its box, never a
    highlighter, never a stroke more than three times its size, and only
    what *Erases* allows.
  - A scribble over nothing is ink like any other. Highlighter strokes and
    shapes a hold produced are never scribbles.
- **Tap existing ink to select it.** With *Draws with: Pen only* (the
  `inkPenOnly` preference), a finger tap or a 450 ms stationary hold
  selects the nearest stroke (10 CSS px hit tolerance, topmost wins).
  - A mouse click in Hand mode and a short tap with the lasso select the
    same way.
  - A swipe still scrolls: more than 8 CSS px of movement, a second
    contact, a pen contact or a cancel drops the pending selection.
  - With finger drawing on, armed tools draw at once. Use Hand to
    tap-select.
- The **selection menu** opens after a tap or a lasso ("Edit
  handwriting"), up to two rows of 36 px buttons. The first row never
  changes: Color, Width, Style, Copy, Duplicate, More and Delete.
  - Color, Width and Style open their option row under whichever rows
    show, one at a time.
  - A blank tap, a press off the page or Escape dismisses the menu and
    lets go of the selection. A comment card beside a PDF page
    ([comments.md](comments.md) "In the app") is off the page.
  - **Color** opens the palette's swatches, the highlighter's when every
    selected stroke is a highlighter and the pen's otherwise, and a custom
    colour.
    - The colour every selected stroke shares is ringed, a mix rings none,
      and a colour off the palette shows on the custom swatch. A colour the
      palette holds at two opacities rings only the swatch at the opacity
      the selection shares.
    - A swatch sets its own opacity, and a custom colour keeps each
      stroke's. On pens and highlighters selected together a swatch sets
      the colour alone, so a highlighter stays translucent.
  - **Style** opens its own row: the Style picker, the Line trio and, when
    the selection holds a closed shape, *Fill* ("Fill" below). The picker
    is the strip's, shown when the selection holds pen strokes, and its
    button names the style they share, or *Mixed*.
  - A pill is pressed only when every stroke it applies to matches: the
    brush looks at pens only, the line at every stroke, the fill at the
    closed shapes.
  - Choosing a style re-bakes the selected pens' widths from the pressure
    and tilt they were written with. A pen becomes a brush pen with its
    tapers, a pencil with its shading, or a calligraphy pen with its nib
    (the stroke's own angle, else 45°).
  - A snapped shape takes the style's name and stays constant width, and
    highlighters are left alone. Choosing a line applies to everything
    selected.
  - Each choice is one undo entry, "ink style change", "ink line change"
    or "ink fill change". A restyle that would push the group past its
    byte budget is refused with the usual pill.
  - **More** (⋯, `aria-expanded`) adds the second row, "More edits": Cut,
    Flip horizontal, Flip vertical, New note, Select note (every stroke of
    the selected ink blocks), Show note (jump to the notes pane) and
    Transcribe ("Transcribe a selection" below).
  - Merge into note takes New note's place when the selection is one whole
    group and the page has another, since New note would change nothing
    then.
  - A row holds at most seven buttons, a hard cap for every later
    addition. Whether the second row is open is remembered for the
    session.
  - Edits apply at once and keep pressure and time. A mixed
    pen/highlighter selection gets a width row per kind.
  - Duplicate puts fresh-id copies 12 px right of and below the originals
    and selects them. The 12 px are measured at the zoom of the page or
    sheet the selection is on, from the menu and Ctrl+D alike.
  - A full group refuses a Duplicate, whether by its strokes or by the
    bytes one file holds, and the selection stays on the originals.
  - Flip mirrors the strokes about the selection box's centre, widths and
    timing untouched, the box staying put. A second flip puts them back.
  - One action across several blocks is one undo entry, and every action
    works without a keyboard. A button's title names the chord where one
    exists ("Copy · Ctrl+C").
  - The menu follows scrolling and resizing, flips above or below the
    selection and hides while the selection is off-screen.
  - It keeps inside the scroller the ink is seen through (the PDF viewer,
    the notebook view, or the notes for a sheet among them), and below the
    strip when the strip floats over the top of that scroller.
  - Its tallest state (two rows and a colour row) fits a 740 × 620
    viewport. A text box's menu and a picture's are placed the same way
    (`useSelectionMenuAnchor`).
- **Copy, cut and paste.** Copy (Ctrl+C, or the menu) keeps the selected
  strokes on a handwriting clipboard: in memory for this tab, and as a
  best-effort system clipboard item for other apps ("Client" below).
  - The pill says "Copied 3 strokes." with *Paste* beside it, the way to
    paste without a keyboard. Cut (Ctrl+X) copies and deletes, "Cut 3
    strokes." with *Paste*.
  - Paste puts down fresh-id copies, keeping each stroke's pressure,
    timing and baked width and dropping a piece's `from`.
  - The copied box's top-left goes at the target, and the box is kept
    inside the page. A different kind of surface (a sheet from a PDF page,
    or back) takes them at the same point size.
  - Three ways say where:
    - **Ctrl+V** (or the pill, or More → Paste handwriting) centres the
      copied box on the page nearest the viewport's centre (the sheet in
      view on a notebook). A paper just opened has no such page until its
      pages are laid out, and Ctrl+V pastes nothing meanwhile.
    - A **pen or mouse held still** on the page for 600 ms, with a writing
      tool armed and something on the clipboard, before it has drawn 2 css
      px, opens a one-row menu, *Paste here*, at the point. In the iPad app
      that goes through the shell's `hold`, so not under a shell without
      it.
    - A **right-click** on blank page with a tool armed opens the same row.
  - Fingers never open it: a resting palm must not paste.
  - The pasted strokes join the page's current group as one undo entry,
    "ink paste", and become the selection with the lasso armed.
  - Select all (Ctrl+A, or More) selects every stroke on that page and
    arms the lasso. Duplicate is Ctrl+D as well.
- **New note from selection** (More → New note) moves the selected
  strokes out of their blocks into a new ink block on the same page, their
  ids kept, as one undo entry ("ink split").
  - A selection that is already a whole note of its own is left as it is
    ("This handwriting is already a note of its own.").
  - Strokes of more bytes than one file holds are refused with the byte
    budget's pill, and the selection stays as it was.
- **Merge into note** appends this group's strokes to the page's previous
  group: the nearest one before it in the page's order, else the first
  after it. It shows in New note's place when the selection is one whole
  group and the page has another.
  - It empties this one, whose block then goes when it holds no caption or
    notes. One that does is left as it is and the pill says "Not merged:
    this note has a caption or notes under it."
  - A merge that would put more strokes or bytes in the previous group
    than one file holds is refused on the pill, and the selection stays on
    this group.
  - Undo brings both groups and the block back together.
- A **finger drag inside the selection** moves it, even in pen-only mode
  (the dashed box is a `touch-action: none` hit surface; fingers outside
  it scroll). A pen with a writing tool clears the selection and writes.
  - The box has a bottom-right **resize** handle and a top-right **rotate**
    handle for mouse, pen and touch. Both act around the selection centre,
    preview while dragging and commit one undo entry on release.
  - Resize keeps proportions and scales the stroke width, and rotate does
    not. Shift snaps rotation to 15°, and a focused handle takes arrow keys
    (10% / 15°).
  - The handles keep their screen size and follow the live preview.
    Pressure, timing, tilt and stroke ids are untouched.
  - A selected picture has the same two handles, with its own rules for
    the value they make ([pictures.md](pictures.md#the-handles)).
- **Editable shapes.** A selection of exactly one stroke that carries a
  shape record (a held shape, a Shapes stroke, a ruler line or a stamped
  one) also shows the shape's own handles, 28 px on the screen.
  - The handles are the two ends of a line or an arrow and the corners of
    a rectangle, a triangle or a polygon. An ellipse has two axis handles,
    the end of its long axis and of its short one.
  - Dragging one reshapes the shape:
    - a line's or an arrow's other end stays (Shift turns the dragged end
      in 15° steps about it);
    - a triangle's other corners stay;
    - a rectangle keeps its angle and its opposite corner;
    - a polygon stays regular and turns and grows with its corner;
    - an ellipse turns and stretches with its long-axis handle and widens
      with the other.
  - The shape is redrawn under the pointer, and the release is one undo
    entry, "ink shape edit", the stroke's id, look and timing kept. A
    focused shape handle takes the arrow keys (1 pt, Shift 10 pt).
  - An erased piece of a shape is plain ink and has none, and a selection
    of more than one stroke has the box's handles only.
- **Fill.** A closed shape (a rectangle, an ellipse, a triangle or a
  polygon) can be filled.
  - The selection's Style row offers *Fill*: *None*, the highlighter
    palette's swatches (the "Ink highlight" colours by default) and a
    custom colour. The fill is drawn as a light tint, a quarter opaque,
    inside the outline and behind it.
  - The Shapes tool's *Fill* fills its rectangles and ellipses with the
    pen's colour.
  - Exports and the AI's picture draw the fill.
  - The annotated PDF keeps a filled shape as its outline, a plain /Ink
    annotation, with the fill only in Gamma's own copy of the strokes.
    Another PDF reader shows the outline, and a Gamma import brings the
    fill back.
- **Hover feedback:** a mouse or hovering pen shows the tool's footprint
  around a centre mark.
  - With *Hover preview* on (this device's `inkHoverPreview`, on by
    default) the footprint is the armed preset's look:
    - a round dot for a pen, a ballpoint or a highlighter;
    - a teardrop for a brush pen;
    - for a pencil, an ellipse that leans the way the pencil does when the
      pointer reports tilt (a round dot otherwise);
    - for a calligraphy pen, its nib (a short bar at the angle the next
      stroke would take);
    - the ring dashed or dotted when the preset's line is.
  - Off, every pen and highlighter shows a plain round ring at its width.
    The Shapes tool shows a crosshair.
  - Pen and highlighter widths follow the zoom. The eraser radius stays in
    screen pixels, like the erasure.
  - Tool and menu buttons show their description on hover or keyboard
    focus.
- **The pencil** draws graphite: the line is filled with a fine grain,
  darker where the pencil pressed harder (three shades on the page) and
  wider where it leaned.
  - Under the pen the live line is stamped from small round dabs of the
    same grain, as wide as the saved line.
  - Exports and the AI's picture draw the same grain in the same shades.
    The exceptions are a dashed or dotted pencil, one dashed line at its
    mean shade, and the annotated PDF's `/Ink` annotation, which a PDF
    reader draws as a plain line.
- **Calligraphy** (the Style picker's fifth row) is a flat nib: the line
  is as wide as the nib where the pen moves across it and a hairline (an
  eighth of that) where it moves along it. Its ends and turns show the
  nib's slanted corners, and a mouse or finger draws the same.
  - The Style row's **Nib angle**: − and + around a readout, −60° to 60°
    in 15° steps (45° to start, the nib rising to the right), beside a
    small nib turned to the angle.
  - A calligraphy pen smooths at 2 unless its *Smoothing* is set, Off
    included. That is a default, never written into the preset, so a pen
    stepped or switched through Calligraphy to another style smooths as
    it did before.
  - When the pen reports its barrel roll (an Apple Pencil Pro in the iPad
    app, or a pen whose browser reports a twist), rolling it turns the
    nib. The roll when a stroke starts is added to the angle (a clockwise
    roll turns the nib clockwise).
  - In the iPad app the row has a **Lock angle** pill, off to start, which
    keeps the angle whatever the roll.
  - The hover footprint is the nib, a short bar at its angle. A stroke
    keeps its nib through every edit: a flip mirrors it, a turn turns it
    with the stroke, an erased piece keeps it.
  - Exports and the AI's picture draw the same nib, its slanted ends and
    corners included. The annotated PDF's `/Ink` annotation is a plain
    line.
- **Live co-drawing.** While someone else draws on a page this tab has
  open, their stroke appears as it is written, in their preset's look.
  There is nothing to switch on.
  - When their drawing is saved the wet stroke becomes the saved one, the
    same line. The freehand trace of a stroke that snapped (a held shape, a
    straightened highlight) gives way to the shape instead.
  - A stroke they undo or erase before it is saved goes at once. One whose
    save never arrives goes a minute after its pen lifted.
  - Every wet stroke of someone who leaves goes with them.
  - A stroke written in the iPad app, and a Shapes stroke, shows once it
    is saved.
- **Dense pages**: a group of more than 200 strokes appears first as thin
  lines that fill out to their true widths within a moment. Groups
  scrolled far off the visible part of a page are not drawn.
  - On a page holding more than 1 500 strokes each finished group is shown
    as a picture (as many as fit in about 64 MB of memory a page, the
    groups in view first). It is redrawn when it is edited or the zoom
    changes, so writing stays quick.
  - None of this changes what is saved.
- **Unsaved handwriting comes back.** What was drawn but not yet saved
  when a tab closed (a crash, a lost connection) is kept in this browser
  for a week.
  - On the next visit it is restored into the page and saved. The pill
    says "Unsaved handwriting from a previous visit was restored." with
    *Discard* beside it, which takes it off again (an undoable step).
  - Strokes someone deleted meanwhile are not brought back, not even those
    the closing tab's last save had carried to the server without hearing
    back, nor those it had only restyled.
  - What a tab still open elsewhere has not saved yet is left to that tab.
- **Undo / Redo buttons** on the strip (*Undo ink* / *Redo ink*) step the
  stroke history. Their disabled state follows it and resets on leaving
  the page, and selecting alone is not an entry.
  - While the Text tool is armed, a text box is selected or edited, or a
    picture is selected, they read *Undo* / *Redo* and step the page's
    block history. They stay
    enabled, since the block history keeps no count, and a step with
    nothing to undo says "Nothing to undo in notes."
  - A press on them never takes the focus from an open editor, a box's or
    a note's. In the block-history mode the step is then that editor's, as
    Ctrl+Z in it would be.
  - **Undo with fingers** (`inkFingerUndo`, this device's: Off · Tap ·
    Double tap; Double tap by default on a touch screen, Off elsewhere):
    two fingers tapped together undo, three redo. They are routed exactly
    as the strip's buttons are (the stroke history, or the block history
    while the Text tool, a text box or a picture has it).
    - A chord is two or three touches that land within 80 ms, move under
      10 css px and all lift within 250 ms.
    - A pinch or a scroll cancels its touches rather than lifting them, so
      it never undoes, and touches while a pen is down are a palm.
    - It works wherever handwriting can be drawn, strip open or not. With
      nothing to undo it does nothing.
    - The first chord of a session says "Two fingers undid a stroke." with
      a *Turn off* action.
- **Settings** for handwriting sit under Reading & editing
  ([settings.md](settings.md) "Reading & editing"). More → *Handwriting
  settings…* opens the section.
  - **Handwriting**, the account's: *Reset presets* (with a confirm),
    *Eraser returns to the last tool*, *Erases*, *Draw and hold*,
    *Scribble to erase*. The presets themselves (`inkTools`) are the
    account's too, though they are edited on the strip.
  - **This device**, this browser's: *Draws with*, *Stylus draws right
    away*, *Pressure-sensitive strokes*, *Undo with fingers*, *Hover
    preview*.
  - **Apple Pencil**, in the iPad app only, this browser's too: *Double
    tap* and *Squeeze*, what the Pencil's gestures do ([ipad.md](ipad.md)
    "The pen"), and *Haptics*.
  - Set on the strip and the zoom window's panel instead: presets,
    smoothing, the straight highlighter and the nib angle, the Shapes
    tool's kind and fill, the ruler's units and the zoom window's height.
    The Shapes tool's are the account's (`inkShapeKind` and
    `inkShapeFill`), the ruler's units and the zoom window's height this
    device's (`inkRulerUnits` and `inkZoomHeight`).
- **A stylus draws right away** even with the strip closed (Settings →
  Reading & editing → This device; on by default), with the last pen
  preset armed on the strip. The pen's eraser end and barrel button erase.
  - **Fingers never draw** when *Draws with* is *Pen only* (`inkPenOnly`,
    default on touch screens): they keep scrolling and pinch-zooming.
  - The first finger that scrolls a page while a tool is armed says so on
    the status pill, "Fingers scroll while a pen draws", with **Draw with
    finger** (which turns `inkPenOnly` off). It says so once a session,
    and never after a pen has drawn.
- Strokes on one page join the **current group** until a stroke on
  another page, or leaving the page. A group is one block in the notes: a
  rounded pen marker, the strokes as a picture, and the block's text as
  its caption (children allowed).
  - The marker or the card scrolls the PDF to the group and outlines it
    briefly.
  - A click on ink selects it (Show note scrolls the notes to its block),
    and in read-only views jumps to the block directly.
- **Replay.** The play button in the card's corner (on hover; always on a
  touch screen) replays the group's writing in the card and on its page.
  - The page scrolls into view: the PDF page, the notebook view's sheet,
    or the sheet in the notes.
  - The strokes appear in the order they were written, each at the pace it
    was written. Pauses shrink to 0.4 s, and a replay longer than 15 s
    plays faster.
  - The same button stops it, and one replay at a time plays.
  - A sheet in the notes view has the button under it, for all its
    handwriting ([notebooks.md](notebooks.md)). On the iPad it is on a
    group's row in the notes and plays on the page ([ipad.md](ipad.md)).
  - A replay shows the drawing as it stands: erased strokes are gone, and
    the pieces the partial eraser left keep the time they were written.
  - A recording made while writing replays the same strokes in step with
    its audio ([recording.md](recording.md)): the replay button and the
    recording's player run on one store.
- **Transcribe with AI**, in a group's ⋮⋮ menu, attaches the block to the
  chat and asks for its text in the caption ([ai_tools.md](ai_tools.md)
  "view_ink"). The chat's "Attach what I see" sends the visible page with
  the handwriting on it, and the agent's `clip_region` stores a picture of
  a group for the notes ([ai.md](ai.md#pictures)).
- **Transcribe a selection** (the selection menu's second row,
  *Transcribe*): a selection made of whole groups attaches them and asks
  for their text, as *Transcribe with AI* does.
  - One that takes only part of a group sends just the selected strokes,
    as a picture of them where they lie on their page or sheet, with
    "Transcribe the selected handwriting." Strokes not saved yet go too.
  - The composer shows a chip per group, "Selected handwriting · 4
    strokes", and the answer comes in the chat. An agent that may edit
    adds the text to that group's caption, as for a whole group.
  - A selection of more than 64 KB of handwriting, or across more than
    eight groups, is refused, "Too much handwriting selected to
    transcribe; select less."
  - A shared page's view has no chat to ask: there the button says so on
    the pill.
- A group erased empty deletes its block, and undo brings it back. A
  group whose block holds a caption or notes keeps its block, with an
  empty drawing, so erasing strokes never deletes text.
- Read-only views (workspace viewers, view shares) show ink without tools,
  and edit shares draw.
- On an iPad this layer runs in Safari or the web app installed to the
  home screen. The iPad app captures the Pencil natively and writes the
  same files ([ipad.md](ipad.md) "The pen"), the calligraphy nib, its
  barrel roll and the ruler's edge included.

## Model

A stroke is a tool, a look and a polyline of samples. Two tools are
known, `pen` (a filled outline) and `highlighter` (a stroked polyline,
multiplied onto the page and drawn under the ink). The pen has four
brushes, `monoline` (Ballpoint, a pen of constant width), `brush`,
`pencil` and `calligraphy`, the absent brush being the plain Pen. Both
are open strings in the file, so a later pen style is a new value and a
renderer that does not know it draws the stroke plainly from its baked
width (below, "Width"). Pressure, timing and tilt stay in the samples,
raw. The width a pressure brush drew with is baked into the samples once,
when the stroke is finished, as a multiplier of the stroke's nominal
size, and the pencil's shading the same way as an opacity multiplier.
Live canvas, retained SVG, eraser geometry, hit tests and the server's
exporters all draw from those baked values, with no width or opacity
formula in any of them, and partial erasing keeps them in every
surviving piece. The one shape built from geometry instead is the
calligraphy nib, which the client's screen and the server's exporters
sweep along a stroke from its angle and size ("The brush table").

**The brush table**, in words (the numbers are pinned by
`tests/shared/inkwidth.json`, whose `about` is the normative statement):

- **Pen** (no `brush`): `w = 1 + (p − 0.5)`, half the size at no
  pressure, one and a half at full, perfect-freehand's on-screen value at
  thinning 0.5. Tilt is ignored.
- **Ballpoint** (`monoline`): no `w`, the line is the size everywhere.
- **Brush** (`brush`): `w = 2p`, shaped by two tapers along the stroke's
  arc length and kept between 0.1 and 2.0.
  - The start taper is two sizes long and the end taper three sizes long,
    each at most half the stroke's length, both eased out as a sine.
  - So a mark shorter than the two tapers (a comma) still reaches `2p` in
    its middle. A stroke of no length (a tap, a dot) is not tapered at all
    and draws `2p`, as perfect-freehand draws a lone point at its
    untapered radius.
  - The stroke in progress keeps the full start taper (its length is not
    known yet) and has no end taper. Both are settled at the lift.
  - When the pointer reports no pressure (a mouse or finger, or the
    pressure preference off) the pressure is simulated from the spacing of
    the samples. The simulation is perfect-freehand's `simulatePressure`
    step (rate 0.275 from 0.5; not its averaging over the first ten
    points, which sets its starting value).
  - Slow is wide, fast is thin, and time is not read, so a mouse brush
    stroke carries `w` without `p`: the only felt look without a stylus.
  - The spacing is read in page points as the samples are. So the same
    hand movement draws a mouse Brush wider zoomed in than zoomed out, and
    wider with a mouse that reports more often.
- **Pencil** (`pencil`): `w = (1 + (p − 0.5)) × (1 + 2 × (1 − a / 90))`,
  the pen's width grown by up to three times as the altitude `a` falls
  from upright (90°, the default when no tilt was reported) to flat,
  capped at 4.
  - `o = 0.15 + 0.85p` is the opacity multiplier, written in the `o`
    channel, which no other brush writes.
  - A mouse or finger pencil stroke writes neither.
- **Calligraphy** (`calligraphy`, a flat nib): `w = max(0.12, s) ×
  (0.85 + 0.3p)`, capped to [0, 4], where `s` is the sine of the angle
  between the stroke's travel at the sample and the nib's edge,
  `|dx·sin(nib) + dy·cos(nib)| / |(dx, dy)|`.
  - The travel `(dx, dy)` (page points, y down) runs from the sample
    before to the sample after (from the first to the second at the start,
    from the last but one to the last at the end).
  - Where those two coincide, a stroke turning straight back, it is the
    segment ending at the sample, else the one starting there. A sample
    with no travel at all (a tap, every sample at one point) takes
    `s = 1`.
  - The edge is the page vector `(cos nib, −sin nib)`, `nib` the stroke's
    angle in degrees counterclockwise as seen (45 when it has none). So a
    45° nib rises to the right and a stroke drawn down to the right across
    it is at full width.
  - The pressure counts only when the stroke reads it. A mouse or finger
    stroke (`pen: false`) takes p = 0.5 and so carries `w` from the
    direction alone, written `xytw`.
  - The tilt is not read, and the barrel roll only sets the stroke's
    `nib`, at its first sample.
  - The client's screen and the server's exporters draw the nib itself
    along the same samples (`nibOutline`, "Client"; `nib_outline`,
    "Server"). Its width across the travel is `size × s`, with the 0.12
    floor kept as a hairline, at every pressure.
  - The pressure's share, 0.85 to 1.15, is in `w` alone: the hit test, the
    eraser, the bounds and the annotated PDF's `/Ink` width read it, and
    the iPad's live line draws it with round ends.
- A **highlighter** has no brushes and no `w`. A **mouse or finger** Pen
  or Ballpoint stroke (`pen: false`) is even.
- A stroke that carries a **`shape`** record (a held shape, a straight
  highlight, a Shapes stroke, a ruler line, a stamped line) is **constant
  width whatever its brush**: no law runs on it. So it has no `w` and no
  `o` and draws its size end to end, a calligraphy shape too (as an even
  line, not with its nib).
  - It keeps its brush key, so the Style picker still names it and a
    restyle changes it, and it is written `xyt`.
  - The Brush's tapers would put both samples of a held straight line at
    the 0.1 floor, and every server renderer draws between a stroke's
    samples. The exports and the AI's picture would show a hairline.

**Dash geometry** is never stored: a stroke's `dash` is `dashed` or
`dotted` and the pattern comes from its `size`. Dashed is on two sizes,
off two sizes; dotted is on a tenth of a size, off two sizes. The caps
are round, with phase 0 at every stroke's and every cut piece's first
sample (`tests/shared/inkdash.json`). A constant-width stroke is dashed as a
stroked line; a pressure stroke is cut by arc length into on-runs that
are each outlined, and a dotted pressure stroke is a circle per dot of
the drawn diameter there.

The storage shape is a deliberate copy of what the two reference apps
store ([research/ink-storage.md](../research/ink-storage.md)). From
Notability come a base width times a per-anchor fractional width, with
the raw force and tilt beside it. From GoodNotes come the explicit
highlighter flag and parametric shape records.

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
(a version 1 file is converted, "Upgrading version 1" below). `space`
is where the strokes lie. Its `kind` is `pdf-page` or `canvas`, a literal:
a third kind would be a version 3. `page` is 1-based, required for a PDF
page. `width`/`height` are the page as displayed at scale 1, in the pdf.js
viewport: points, origin top-left, y down, rotation applied. That is the
frame highlight rects normalise to, and the frame the PDF writers map to
user space. `{kind: "canvas", width, height}` is a sheet of paper, points
from its top-left corner, the sheet's size when the group was drawn
([notebooks.md](notebooks.md)). `strokes` is the list below. Any other key
of the file or of `space` is kept verbatim by every reader and writer
(parse, canonical bytes, merge, the exporters' `/GammaInk`), up to 16 KB of
serialised extras per file. On a merge the result takes the writer's
file-level extras: the last writer by strokes, advisory only, since
nothing writes one.

**A stroke.** Every key but `id` and `pts` has a default or is omitted,
and the canonical bytes never write a key at its omission value:

| key | type | default / omitted when | meaning |
|---|---|---|---|
| `id` | `^[A-Za-z0-9_-]{1,32}$` | required; a client mints 8 chars | survives every edit (the merge's handle) |
| `tool` | `^[a-z][a-z0-9-]{0,15}$` | `pen` | `pen` or `highlighter` are known; an unknown value draws as a round-capped polyline of the baked width, source-over at `opacity`, in its own export bucket; the eraser and the lasso take it like any stroke |
| `brush` | `^[a-z][a-z0-9-]{0,15}$` | omitted | `monoline` (Ballpoint: constant width, no `w`), `brush` (Brush: `w` from pressure or speed with tapers), `pencil` (Pencil: `w` from pressure and tilt, `o` from pressure) and `calligraphy` (Calligraphy: `w` from the travel against `nib`, a little from pressure) are known; a stroke with a `shape` keeps its brush but has no `w` or `o`, whatever the brush; an unknown brush draws exactly as a pen from `w` (and `o`), so only texture is lost. Open on every tool: the server does not refuse one on a highlighter (it draws at its size, as any highlighter) |
| `color` | `#rrggbb` or `rgb[a](…)`, ≤ 40 chars | `#1f1f1f` | |
| `size` | number in (0, 100] | `1.6` | nominal diameter in pt, the anchor `w` multiplies; a width restyle changes `size` only, a resize scales it; the server refuses `size × max(w) > 100` |
| `opacity` | number in (0, 1] | `1` | |
| `pen` | bool | `true` | `false` marks a stroke without real pressure (a mouse or finger, or a stylus with *Pressure-sensitive strokes* off), drawn even, no `w`, for every brush but `brush`, whose `w` then comes from the pointer's speed, and `calligraphy`, whose `w` comes from its direction alone (a snapped stroke is `pen: false` too, and even whatever its brush: it carries `shape`) |
| `t0` | int ≥ 0 | omitted | wall-clock ms of the first sample, never invented (a client without timing omits `t` and `t0`); the replay reads both |
| `band` | `0` or `1` | the tool's default, `0` for a highlighter and `1` otherwise, and omitted when equal to it | `0` draws under, `1` draws as ink. Every layer draws band 0 first, then band 1 (`InkLayer`, `to_svg`, `pdf_path_ops`, the annotated PDF's `/Annots` order); a tap prefers band 1 within the radius (`nearestInkStroke`) |
| `from` | a stroke id | omitted | the stroke this piece was cut from by the partial eraser. A cut of a piece keeps the root's id, so every piece names the original. Written by `restroke`, kept by every edit, dropped on paste and duplicate (`cloneStrokes`, `duplicateStrokes`: a copy is not a piece). Read by the merge only |
| `shape` | `{kind, params}` | omitted | `kind` one of `line` `arrow` `rect` `ellipse` `triangle` `polygon` `curve`; `params` an object of at most 64 finite numbers (and booleans), the geometry an editor rebuilds `pts` from after a handle drag, moved with `pts` by every edit that moves the samples ("Client"). The stroke **also carries its baked `pts`**, so hit test, bounds, eraser, lasso, replay and every exporter draw it as any stroke; it is constant width whatever its `brush` (no `w`, no `o`: "The brush table"); a piece cut from a shape loses `shape` and `fill` |
| `fill` | `{color: '#rrggbb', opacity}` | omitted; `opacity` in (0, 1], omitted at `0.25` | the inside of a closed shape (a `shape` of kind `rect`, `ellipse`, `triangle` or `polygon`: `CLOSED_SHAPES`, both languages), drawn under its outline at `opacity`; written only there (the selection's Fill, the Shapes tool's), and a fill on any other stroke is kept but not drawn; a piece cut from the shape drops it |
| `dash` | `dashed` or `dotted` | omitted (solid) | dash geometry is derived from `size` at render time, never stored ("Dash geometry" above; `DASH` in `ink.js` and `ink.py`) |
| `nib` | int 0..179 | omitted | a calligraphy nib's angle in degrees, counterclockwise as seen: the nib's edge is the page vector `(cos nib, −sin nib)`, so 45 rises to the right. Written on every calligraphy stroke, 0 included (`!= null`), and only there: a brush change away from `calligraphy` drops it, one to it writes the stroke's own or 45. Kept by a cut, mirrored by a flip (`(180 − nib) mod 180`) and turned with the samples by a rotation; part of the stroke's geometry in the merge |
| `ch` | `^xy[a-z]*$`, no repeated letter, ≤ 12 chars | `xy` | the channels of each sample, below |
| `pts` | non-empty list of ints, a multiple of `len(ch)` long | required | the samples, flat |
| anything else | JSON to depth 3, finite numbers, keys `^[A-Za-z][A-Za-z0-9_]{0,31}$`, strings ≤ 1 KB, arrays ≤ 256 | — | kept verbatim through parse, canonical bytes, merge and every edit helper (`restroke`, `duplicateStrokes`, `cloneStrokes`, `translateStrokes`, `transformStrokes`, `restyleStrokes`); ≤ 1 KB serialised per stroke. A null-valued key is dropped |

The server's models are pydantic with `extra="allow"` and one extras
validator for the caps (`gamma/ink.py`). `json.loads` runs with a
`parse_constant` that refuses `NaN` and `Infinity`, and `dumps` refuses a
number that is not finite (`ValueError`). Pydantic would keep a `NaN`
extra, and `JSON.parse` in every browser then fails on the file. The
extras validator also refuses an integer past the largest double, which
a browser reads as `Infinity`. Whatever the parser itself refuses (an
integer past Python's 4300 digits, nesting deeper than its stack) is an
`InkError` like the rest (`load_json`): an upload answers 400 and a
stored file reads as unreadable.

**Channels.** `ch` names the channels of each sample, InkML-style, `x`
`y` first and then any of the letters below in any order; `pts` is one
flat integer array, `len(ch)` integers per sample, ~300 bytes a stroke.

| letter | unit | encoding |
|---|---|---|
| `x` `y` | 1/100 pt | delta after the first sample; the running sums are checked to ±10⁹ (`MAX_XY`, ±10 000 000 pt: a hundred times the largest page, and every writer can place it, where a PDF's numbers stop at 28 digits) |
| `p` | pressure 0..1000 | absolute; decoded as `p / 1000` and clamped to 0..1 |
| `t` | ms since `t0` | delta after the first sample |
| `a` | altitude, degrees 0..90 | absolute |
| `z` | azimuth, degrees 0..359 | absolute |
| `w` | drawn-width multiplier × 1000 | delta after the first sample; the running sum is checked to 0..4000 (decoded 0..4.000, `1000` = the nominal size; the pencil's slant reaches 3.5, and the drawn ceiling stays `size × max(w) ≤ 100`) |
| `o` | drawn opacity × 1000 | delta after the first sample; running sum 0..1000 (decoded 0..1.000); written by the pencil. The rule is `opacity × o` (`strokeOpacity` / `stroke_opacity`, one line each, like the width, pinned by `inkwidth.json`); the renderers draw it in three shades, `o` rounded up to 0.33, 0.66 or 1 (`opacityBand`, `stroke_bands`), each shade at `opacity × band` |

A letter neither codec knows is carried by stride: `decodeStroke` /
`decode_stroke` put its integer in the sample's `rest` array, and every
reader ignores it. Both encoders copy `rest` back in place, so a cut on a
copy that predates a letter keeps the stride and the file stays valid. The
decoded sample is `{x, y, p?, t?, a?, z?, w?, o?, rest?}`: each key is
present when its channel is, and `rest` when the list has a letter the
codec does not know. `w` and `o` are multipliers, so a decoded cache never
depends on `size`, and `tests/shared/inkwidth.json` pins the decoded
values.

**Rounding.** Both encoders (`encodePoints`, `encode_points`) round every
channel as `Math.round` does, a half going up (`js_round` on the server).
They clamp `p` and `o` to 0..1, `w` to 0..4 and `a` to 0..90. A channel a
sample lacks, or holds as null, takes its default: `p` 0.5, `t` 0, `a`
90, `z` 0, `w` 1, `o` 1, and 0 for a letter the codec does not know.
`tests/shared/inkcodec.json` pins the two to the same integers.

**Width.** The drawn width at a sample is `size × w` when the stroke has
the `w` channel, else `size`. That is the whole rule: there is no width
formula in rendering, hit-testing, bounds, the exporters or the server
(`strokeWidth(stroke, sample)` in `ink.js`, `stroke_width(stroke, sample)`
in `ink.py`, both one line). The opacity is `opacity × o` with the `o`
channel, else `opacity` (`strokeOpacity`, `stroke_opacity`, the same one
line), which every renderer draws in three shades (`o` rounded up to a
band, "Channels" above), so no law hides there either. Pressure-shaped
strokes (tool `pen` with `pen` true and no `brush`, or brush `brush`,
`pencil` or `calligraphy`, and no `shape`) always carry `w`. It is baked
once on the client in `finishStroke.js` `bakeWidths` from the brush table
(`BRUSHES`, "The brush table" above), the only place a width or opacity
law lives, and a brush restyle of existing strokes runs the same
function again (`restyleBrush`, "Client"). Constant-width strokes omit
`w`: a highlighter, monoline, a `pen: false` Pen, Ballpoint or Pencil, and
every stroke with a `shape`, whatever its brush. A freehand `pen: false`
Brush stroke carries `w` from the speed law and no `p`, and a `pen: false`
calligraphy stroke `w` from its direction alone. The raw `p`, `a`, `z`
stay beside `w`, as Notability keeps force and tilt beside its fractional
widths, so a brush change re-bakes from them and a future law can re-map.
The multiplier form means a width restyle touches `size` alone, and `pts`
stay byte-identical. perfect-freehand is driven from `w`:
pressure = `w / 2` with thinning 1 (perfect-freehand does not clamp the
pressure, so a pencil's `w` of 3.5 draws a radius of 1.75 sizes), and
the outline's radius matches the baked width exactly. The live
canvas runs the same function on the samples' live `w`, computed by the
same law (a brush's end taper is applied at the lift, so the wet line has
its start taper only). Tilt is captured for pen pointers only
(`inkSample`: `altitudeAngle` / `azimuthAngle`, else `tiltX` / `tiltY`,
as integer degrees). The `a` / `z` letters are written only when at
least one sample of the stroke reported tilt, so a tilt-less client and a
tilt-capable one hash an untilted stroke identically. A sample without
tilt inside such a stroke writes the defaults, 90 and 0 ("Rounding"
above). The iPad shell sends raw samples and the web app bakes them like
its own ([ipad.md](ipad.md) "The pen").

**Limits** (`gamma/ink.py`, enforced on upload): 5 000 strokes, 500 000
samples and `MAX_BYTES` 6 MB a file. Numbers are finite (`pts` integers
included), stroke ids unique and the extras within their caps. x and y
lie within ±10 000 000 pt, `w` in 0..4 and `o` in 0..1, with
`size × max(w) ≤ 100`.

**The bytes of an upload** are the client's, `ink.js` `serializeInk`: keys
sorted at every level, no whitespace, null and undefined keys dropped, and
every number as `JSON.stringify` writes it (`612`, never `612.0`; `1e-7`,
`0.00001`, `1e+21`). The server validates an upload and stores it as it
came, named by the sha256 of those bytes (`routers/ink.py`,
`storage.store_file`). So the same strokes from a browser, the iPad and
the migration are one file, and a client can name a file by its hash
before the server has it. The server writes files of its own with
`ink.dumps`, for a merge, an import or the upgrade step. It is the same
canon. Numbers go through `_js_number` (an integer past 2^53 as the
double a client's `JSON.parse` reads), and non-ASCII text in an extra key
is UTF-8 on both sides, never `\u`-escaped. The codec lives twice by design
(`ink.py` `decode_stroke` / `encode_points`, `ink.js` `decodeStroke` /
`encodeStroke`). `tests/shared/inkv2.json`, `inkwidth.json`,
`inkmerge.json` and `inkcodec.json` pin both sides to the same bytes.

**The byte budget** is the client's to keep. `applyInk` measures the
group's bytes (`inkBytes`: exactly `serializeInk(ink).length`, summed
from each stroke's canonical bytes and cached per stroke object, so an
edit costs its changed strokes only). It refuses an edit that would cross
`MAX_BYTES` with the pill "This handwriting note is full. Move part of it
to a new note, or start a new one." A new stroke that would push a group
past 3 MB starts a new group block on the same surface instead (a group
is "the page's current writing", so this is invisible). `finishStroke`
decimates coalesced samples closer than 0.1 pt to the previous kept one
before baking, so a 240 Hz trace roughly halves with no visible change.

### Upgrading version 1

A one-time workspace step upgrades every stored file. Version 1 is read
only by one pure converter, `ink.upgrade_v1` (Python) and `upgradeV1`
(JavaScript), deterministic and pinned byte for byte by
`tests/shared/inkv2.json` (v1 input bytes and the exact v2 output). The
law:

- `version` becomes 2, and a file without a `version` counts as 1.
- For every stroke whose tool is `pen` (or absent), `pen` is not
  `false`, `brush` is absent and `ch` has `p` but no `w`, `w` is appended
  to `ch`. Each sample gains `w = 500 + clamp(p, 0, 1000)` (the
  multiplier `1 + (p / 1000 − 0.5)` times 1000), absolute on the first
  sample and a delta after it.
- Every other stroke (no `p`, monoline, highlighter, `pen: false`) and
  every other key is unchanged, and the output is the canon above.
- Anything that is not version 1 (version 2, a newer version, a
  malformed one) comes back as it is, so the converter can never rewrite
  a newer file down to 2. `parse_ink` judges it.

The baked law is the width the screen drew a version 1 stroke at
(perfect-freehand at thinning 0.5 draws `1 + (p − 0.5)`), so a converted
stroke exports as it looked on screen.

Version 1 is handled in these places and nowhere else:

- **Workspace migration step 41 `ink_v2`** ([migrations.md](migrations.md)
  step 41, `db.SCHEMA_VERSION` 41): every block's version 1 (or
  unversioned) file becomes a **new** upload named by the sha256 of its
  `ink.dumps` bytes, and the block's `ink_url` names it. A version 2 file
  is skipped, so the step is re-runnable.
  - Because the conversion is pure, every copy of a workspace (the server,
    a desktop mirror, the iPad) produces the same hashes, so the mirror's
    digest sync agrees once both have run it. A mirror pair pauses until
    then ([mirror.md](mirror.md) "The data format").
  - Writing upload files is the documented exception to migration rule 4.
- **`POST /api/upload-ink`** and the mirror's `POST /api/upload-file`
  refuse version 1 with `400 {detail: "update Gamma"}` (`routers/ink.py`
  `parse_upload`): after the step every writer is v2.
- **The PDF importer** (`routers/imports.py`, through `ink.from_pdf_ink`)
  converts the `/GammaInk` payload of an annotated PDF exported before
  the upgrade, then stores it. Exports write the v2 strokes verbatim into
  `/GammaInk`.
- **Backup restore** runs the step like any other (migration rule 7), so
  a zip made before the upgrade comes back converted.
- **The client** converts a v1 file it is served (a copy that has not
  run the step yet) as it loads it, so it renders: `ink.js` `loadedInk`,
  which `inkStore.loadInk`, inkStore's restore fetch and the journal's
  `restoredInk` share. It never re-uploads the file as v1: the next
  edit's upload is v2 and the stored file is replaced by that write.

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

- A stroke is compared as two halves.
  - Its **geometry** is `pts`, `ch`, `size`, `shape`, `brush` and `nib`.
    Its **look** is everything else but `id` and `from`: `color`,
    `opacity`, `dash`, `band`, `fill`, `tool`, `pen`, `t0` and any extra
    key.
  - A width restyle is therefore geometry, a move or a resize too, and so
    are a brush change and a nib change (a restyle to calligraphy, a flip
    or a turn of a calligraphy stroke). Each bakes `w` in `pts` again or
    moves the samples with it, so the key travels with the widths baked
    for it.
  - A brush change here then composes with a recolour there into the new
    brush with its widths in the new colour. An undo of it after someone's
    recolour gives the plain widths back with the key.
  - As a change of both halves it would split: the recolour's side kept
    the old key on the new widths, the undo the new key on the old ones.
- Per id, three ways:
  - a half changed by one side only is taken from that side, so one
    writer's move and another's recolour compose;
  - a half changed by both (to different values, on any of its keys)
    takes the stored (theirs) version and marks the merge unclean;
  - a stroke neither side touched is the identical object.
- A stroke the writer added goes after the stroke before it that the
  result keeps, behind strokes the other side added there (theirs comes
  first, as with text). One id added on both sides with different
  content keeps theirs, unclean.
- An edit beats a delete: a stroke one side erased and the other changed
  (either half) stays, unclean. Both erasing it is clean.
- **Pieces follow their parent.** A stroke with `from: X` that one side
  added follows its *parent's* fate on the other side.
  - The parent is the stroke of X's family (X or a piece naming X) that
    this side no longer has. On the other side:
    - the parent unchanged: the pieces stand and the parent goes;
    - its look changed: the pieces take that look (and when the cutting
      side had restyled them too, theirs' look wins, unclean);
    - the parent erased: the pieces are erased too (an undo of the drawing
      wins over a cut of it);
    - its geometry changed, or cut there as well: theirs' version (the
      moved stroke, or theirs' pieces) stands and the writer's pieces are
      dropped, unclean.
  - So a cut against a restyle never draws the ink twice, and a cut
    against an undo leaves nothing behind.
  - A piece whose parent the base never had is an ordinary addition. A cut
    from a copy that writes no `from` merges as additions and an erasure.
- **A base file that is gone** merges by `base_ids` when the write
  carries them. Without `base_ids` it is a union by id.
  - A stroke one side lacks whose id the base had is treated as erased,
    and the merge is marked unclean when such a deletion was applied.
    Deletions win both ways, since "the other side changed it" cannot be
    judged without the base's content.
  - A stroke the base did not have is an addition and stays.
  - A mirror's push uploads the base file first where the origin no longer
    holds it (sync_engine `_push_bases`), so the origin merges three-way.
    It carries `base_ids` too, read from its own copy of the file
    (`_with_base_ids`), for an origin that still lacks it.
  - A file that cannot be read, or a result over the budgets, leaves the
    write as sent (last writer wins).

The merge returns the file, `clean`, and two id lists: `lost_ours`, the
writer's changes the result dropped (an edit or erasure theirs overrode,
pieces that were dropped), and `returned`, the strokes theirs had erased
that the writer's change brings back. `ops._Batch.merge_ink` puts
`{clean, lost_ours, returned}` into the batch result under the block's
id (`ink_conflicts`, one per drawing the batch merged), not into the
echo, so the op log and catch-up never carry it. `routers/blocks.py`
puts its block's onto the `PUT` answer as `ink_conflict`, and
`routers/collab.py` puts them all onto the ops answer. A mirror reads
them there for its own pushes, and an unclean merge becomes an
`ink_merged` conflict row ([mirror.md](mirror.md) "What travels"). A
retry the server answers from its op log (the batch landed and its
answer was lost) carries none. The writer's pill:

- `lost_ours`: "Your change to n strokes was not applied: someone changed
  them since".
- `returned`: "n strokes someone erased came back".
- An unclean answer with neither: "Merged with changes made meanwhile".

Undo and redo collect the same flag (`inkUndo`): "Undone: … (page 1; n
strokes someone changed since were kept)." Each count goes through
`tn()`, so one stroke reads "Your change to 1 stroke was not applied:
someone changed it since", "1 stroke someone erased came back" and "…
1 stroke someone changed since was kept)."

The merged file is stored (`ink.dumps`) only when it is new. One stored
already stays: the purge deletes only under the write lock the batch
holds. The applied op names the merged file, with its stroke count and
box. Merging a change that already landed adds nothing, so a mirror
resends a lost ink push as it is, its `base_ids` with it (sync_engine
`_unlanded`). A merge that
changes nothing returns the side it equals (the space compared on its
known keys), so a resend mints no copy.

The same function runs where a client applies another side's drawing
itself:

- the desktop mirror, through its local server's op path (its diffs
  carry `base_props`, sync_tree `_set_op`). A `400` on an `.ink` push is
  recorded as a per-file `sync_conflicts` row and the page's other ops
  land (sync_engine `_push_files`).
- undo and redo in the browser and on the iPad, which apply an entry's
  change onto the group as it stands, so strokes someone else drew
  meanwhile stay.

Keys other than `ink_url` stay last-writer-wins, except a text box's
`text_box`, merged key by key ([text_boxes.md](text_boxes.md) "Merge").

**Fences**: code, not data, for the copies the format pause does not
reach.

- `flushInk` runs 700 ms after the last edit and at most 5 s after the
  first unsaved one, so files land while writing goes on. It flushes at
  once, with `fetch` `keepalive`, when the tab goes to the background
  (`visibilitychange` to hidden: a phone may freeze or end a hidden tab
  without a `pagehide`).
- **Every group's requests start at once**, none waiting for another
  group's. Nothing is sure to run once a closing page is gone, so a flush
  waiting for each group in turn would send the first alone.
- The groups **share the room the page's keepalive ledger leaves
  handwriting**, its lowest priority (`available('low')`; the ledger and
  its priorities are [collab.md](collab.md) "Keepalive").
  - `keepalive` goes to the groups, in order, whose upload and `PUT`
    bodies still fit in that room, and the others go as plain fetches
    (`ink/inkFlush.js` `planFlush`, "Client").
  - When a closing page cuts a plain fetch off, the draft journal keeps
    what it wrote of that handwriting before the close and restores it on
    the next visit ("Client", the journal). That is why handwriting takes
    the lowest priority: typed edits lost at the close are lost for good.
- `pagehide` flushes every draft still unsaved.
  - A tab closed while it shows fires `pagehide` first and
    `visibilitychange` after it, in one task (every close measured that
    fired them, headless Chromium, WebKit and Firefox:
    [research/keepalive-abort.md](../research/keepalive-abort.md)). So the
    close flushes once, and the flush on going hidden that follows finds
    nothing scheduled.
  - A tab hidden before it closes flushed when it was hidden. Its
    `pagehide` flushes what is still unsaved again, whether or not that
    first flush is still on its way.
  - Firefox can stop the `pagehide` listener before it flushes
    ([collab.md](collab.md) "Keepalive"). The flush on going hidden then
    sends what waits on the save timer, the `PUT` after its upload, and
    the draft journal keeps the drawing.
- On `pagehide` the upload and the block's `PUT` go side by side, since
  nothing is sure to run once the page is gone. The `PUT` names the file
  by the hash `inkStore` worked out when the draft changed (`nameLater`,
  idle time).
  - Only there, because a peer that the `PUT`'s op reaches before the
    upload has landed finds no file.
  - A flush the page will live past keeps the upload-then-`PUT` order. So
    does a draft the server will have to merge (its block names another
    file than the draft's base), even on `pagehide`.
  - So does a draft not named yet. Its `PUT` waits for the upload's
    answer, which a closed page never reads, and the journal brings the
    drawing back on the next visit.
  - A group's first `PUT` can reach the server before the group's insert,
    which the edits' `POST` carries. Its 404 is not retried once the page
    is gone. The block then names no file, and the journal's restore
    brings the drawing back whole ("Client", the journal).
- `pagehide` sends nothing twice.
  - A drawing whose upload the first flush has on its way with keepalive
    sends its `PUT` alone, beside that upload, or nothing when the `PUT`
    cannot go beside it. Then that flush sends it if the page lives, and
    the journal brings the drawing back if it does not.
  - One whose `PUT` is on its way with keepalive sends nothing more.
  - An empty group whose `DELETE` is on its way is not deleted again: a
    second would be answered 404 and raise "Handwriting not saved yet" on
    a page the back-forward cache keeps.
  - The `DELETE`, with no body, always goes with keepalive (collab.md
    "Keepalive"), so it counts as on its way even when the queued edits
    leave handwriting no room.
- Every `PUT` is written to the draft journal before it goes
  (`inkStore.markSending`, "Client"), since its answer may never come.
  - Every journal write is committed as it is made (`commit()`), so one
    made as the page goes lands in Chromium and Firefox. WebKit keeps
    none made then.
  - So the journal writes a draft as it changes. That write, made while
    the page lives, holds a stroke drawn as the tab closes in every
    engine, WebKit too
    ([research/keepalive-abort.md](../research/keepalive-abort.md#the-app-at-a-close)).
  - `journalAll` writes every dirty draft again on `pagehide` and on going
    hidden. Firefox can stop it on `pagehide`, where it is the page's
    first listener.
  - A 4xx stops the flush's retries, except a 404 (the block's insert may
    still be queued) and the ones `collabSession.retryableStatus` retries
    (408, 429). The draft is kept behind the sticky pill "Handwriting not
    saved: <detail>".
  - 5xx and network errors retry after 2 s.
- The page socket's hello and `/api/session` carry the server's `build`.
  The client keeps the build its session first reported and compares
  every hello's with it (`ink/buildFence.js` `noteServerBuild`,
  "Client").
  - On a mismatch it shows "Gamma was updated, reload to keep drawing"
    and refuses to start a stroke or a cut (reads keep working).
  - A tab left open across a server update, or the web app on an iPad home
    screen, runs the old bundle until reloaded.
- `ws_backup` refuses to restore a zip stamped above `SCHEMA_VERSION`,
  and `sync_engine` records a `400` on an `.ink` push per file instead of
  failing the page. The byte budget and the decimation above are fences
  too.

## Client

- `ink/ink.js` (pure): the codec (`encodePoints` / `encodeStroke`,
  `decodeStroke`, the known letters in `KNOWN_CHANNELS`), `strokeWidth`,
  `strokeBand` / `byBand` (a drawing in band order), bounds (`strokeBounds`,
  `inkBounds`, `boundsOf`, `unionBox`), `pdfPositionOf`, the stroke edits,
  the merge and the rendering.
  - It also exports the arithmetic the ink modules share (`clamp`, `mod`,
    `round2`, `round3`, `arcLengths`).
  - `encodedStyle(style)` is the keys of a tool style a stroke is encoded
    with, which InkLayer's `commitStroke` and `commitShape` and `ruler.js`
    `stampLine` use.
  - **The eraser** works on the path it swept.
    - `eraseAlong(ink, path, radius, allow)` is the partial eraser along
      `path` (page points, consecutive points the capsules of the radius,
      one point a disk). It cuts every stroke where its centre line comes
      within the radius plus the drawn half-width there (`strokeWidth` /
      2, linear between two samples).
    - The test runs along each segment between two samples, against each
      capsule (`underCapsule`). The distance from a point moving along a
      segment to a segment is convex, and less a linear half-width it
      stays convex, so their common part is one interval of the segment.
    - That interval is found from a point inside it by a bisection to each
      side to 0.001 pt, ending just outside the edge. The point is where
      the two segments cross, or an end of one against the other, else a
      golden-section search finds one.
    - Boxes (the stroke's, each capsule's, each segment's) rule out nearly
      every pair first.
    - An overlap shallower than the file's 0.01 pt grid is a touch and
      cuts nothing. A cut end rounded onto that grid may lie about that far
      inside the edge, and a sweep over it again (the next flush starts
      where the last one ended) must leave it as it is.
    - The intervals of all capsules are merged (`sweptRanges`), and the
      stretches between them become pieces through `restroke`. Each new
      end is a sample lerped where the stretch ends (`sampleAt`: `x`, `y`,
      `p`, `t`, `w`, `o` linear; tilt and letters this build does not know
      from the nearer sample).
    - So a piece ends at the eraser's edge however far apart the stroke's
      samples or the path's points lie.
    - A piece shorter than max(0.2 pt, half its drawn width at either end)
      is a speck and goes, and a dot (one sample) within reach goes whole.
    - The answer is `{ink, changed}`. A stroke `allow` refuses stays the
      same object, and a sweep that cuts nothing gives the same ink.
    - `eraseAt(ink, x, y, radius, allow)` is it at one point.
    - `hitStrokesAlong(ink, path, radius, keep)` is the whole-stroke
      eraser's test on the same geometry (the strokes the partial eraser
      would cut), `hitStrokes(ink, x, y, radius, keep)` at one point.
    - `frontend/tests/inkErase.test.mjs` pins the cuts.
    - `frontend/tests/bench/inkErase.bench.mjs` (not part of `npm test`)
      times one flush of a 20-point path over a 500-stroke drawing, against
      any copy of `src/ink`, and counts the samples a flush leaves inside
      the swept band. It runs a copy without `eraseAlong` as its layer ran
      it, the point eraser at each of the path's points.
  - `translateStrokes` only touches the first sample's two absolute
    integers (and a shape record's points, below).
  - `transformStrokes` scales/rotates selected XY samples around a shared
    origin without changing the other encoded channels.
  - `strokesInLasso` picks strokes with more than half their samples
    inside the polygon.
  - A pen stroke (any brush) renders as perfect-freehand's outline in one
    filled SVG path (page units; the layer's `viewBox` does the zoom),
    driven by the baked `w` (`outlineOptions`, `outlinePressure`). A
    highlighter renders as a stroked polyline with `mix-blend-mode:
    multiply`, and an unknown tool as a source-over polyline at its mean
    drawn width (`strokePath`).
  - **Dash**: `DASH = {dashed: {on: 2, off: 2}, dotted: {on: 0.1, off:
    2}}` in sizes, and `dashPattern(stroke) → [on, off] | null` in points.
    - A stroked path gets `strokePath(...).dash`, the `stroke-dasharray`
      string (`"4 4"`).
    - A pressure stroke's `d` is built by `dashRuns(samples, stroke) →
      [[samples], …]`: the on-runs by arc length with the boundary samples
      lerped (`x`, `y`, `p`, `t`, `w`; `a`, `z` from the sample before).
      Each run is outlined on its own, round at both ends, and the outlines
      are concatenated.
    - A dotted one is a circle of diameter `size × w` at each run's middle
      (`tests/shared/inkdash.json` pins the runs).
    - A piece of a stroke is outlined by `runOutline(samples, opts, first,
      last)`: perfect-freehand at ten times the page's scale, a two-sample
      piece with its midpoint added first, as the server's
      `stroke_outline` does.
    - Perfect-freehand drops the points within 3 units of a path's end, so
      a piece shorter than 3 pt (a thin pen's dash, a short shade) would
      outline as a disc about its length across. It also draws the points
      it puts between two at pressure 0.5, the nominal size.
    - Every dash of a dashed pressure stroke is such a piece, the only dash
      of a stroke shorter than one dash and a gap included. A solid stroke
      is outlined whole at the page's scale (`penOutline`).
    - The live canvas outlines its dashes the same way, its first while it
      is the only one too.
  - A **pencil** stroke's `d` is three paths, one per opacity band: `o`
    quantised up to 0.33, 0.66 or 1 (`opacityBand`).
    - Consecutive samples of one band form a run with lerped boundaries
      like a dash (`bandRuns`, which leaves out a run of no length, as the
      server's `stroke_bands` does).
    - Each is filled with the layer's grain pattern at `fill-opacity` band
      × `opacity`. `strokePath` returns `{bands: [{d, alpha}]}` for it, the
      runs' inner ends flat so the shades meet without overlapping.
    - Every pencil's shape, a mouse pencil's even outline included, carries
      `grain: true`, which the layer fills with the grain.
  - `strokeOpacity(stroke, sample)` is the exact opacity rule beside
    `strokeWidth`, which the bands round and the fixtures pin.
  - `strokeMaxWidth(stroke)` is the widest it draws, what the server caps
    at 100 pt. The selection's resize stops there and `restyleBrush` holds
    a re-baked `w` there.
  - **Caches**: decoded samples, bounds and outlines are kept per `pts`
    array (`strokeCache`, a WeakMap keyed by the array, with a WeakMap per
    stroke object in front).
    - Each value is also keyed by what else it was built from: the size
      for the bounds, and every key but the colour, `id`, `from` and `t0`
      for an outline (`outlineKey`, so an outline never reads the colour).
    - A value is reused while the stroke keeps that array: a colour
      restyle, `withDefaults`, the merge's composed and untouched strokes,
      and a reload whose strokes `inkStore.adoptStrokes` matched by id.
    - A move, a cut or a brush restyle writes a new array and builds
      again, and so does a change of size, line or opacity on the same
      array.
    - `cachedStrokePath(stroke)` looks without building.
      `strokePlaceholder(stroke)` is the stand-in a dense group draws
      meanwhile (the samples as a stroked polyline at the mean drawn
      width).
    - `frontend/tests/bench/inkCache.bench.mjs` (not part of `npm test`)
      times the outlines of a 500-stroke drawing cold and after a reload, a
      merged save and a restyle, against any copy of `src/ink`.
  - `nearestInkStroke` resolves a touch to the nearest stroke edge (a
    band-1 stroke wins over a band-0 one within the radius, then the
    topmost).
  - `restyleStrokes(ink, ids, patch)` changes selected colour, `size`,
    `opacity` or `dash`, never `pts`, and returns the original object for
    a no-op. The dash is `{dash: 'dashed' | 'dotted' | null}`, null
    clearing it, and any other value is ignored.
  - A brush change is `finishStroke.js` `restyleBrush`, since it re-bakes.
  - `cloneStrokes(strokes, dx, dy, used?)` is the copy every paste and
    duplicate is made of: fresh ids (none in `used`), `from` dropped,
    moved by `dx, dy` pt through the first sample's two absolutes.
    Every other key (`t0`, `p`, `t`, `w`, `shape`, extras) is kept, and
    `duplicateStrokes` is it on a selection.
  - **A shape record follows the samples**: every edit that moves them
    (`translateStrokes`, `transformStrokes`, `cloneStrokes`, so
    `duplicateStrokes` and a paste, and `flipStrokes`) carries the
    stroke's `shape.params` through the same map (`mapShape`). So the
    params an editor rebuilds `pts` from (`shapes.js` `shapeSamples`)
    never snap a moved shape back.
    - Its points (`cx, cy` and every `xN, yN`: a line's or an arrow's
      ends, a triangle's corners, a curve's points) are moved, turned and
      scaled as the samples are. Its lengths (`w`, `h`, `r`, `rx`, `ry`,
      `head`) are multiplied by the scale.
    - Its `angle` is turned (or mirrored) and put back in the range
      `recognise` gives it: a rectangle's [−45°, 45°) with its sides
      swapped across a quarter turn, an ellipse's [0°, 180°), a polygon's
      [0°, 360° / n).
    - Every changed number is on the 1/100 grid. A side count, a flag or
      an unknown key stays as it is.
    - A key the record leaves out is written, when the edit changes it,
      with what `shapes.js` draws in its place. A resize writes an arrow's
      head (`min(length / 4, 24)`) times the scale, and a turn or a flip
      the angle 0 of a rectangle, a polygon or an ellipse.
    - A record of a kind this build does not know is dropped by such an
      edit, the stroke kept as ink. Its params may be geometry it cannot
      move, and stale ones would rebuild the wrong samples.
    - An edit that moves nothing keeps the record, the same object.
    - `frontend/tests/ink.test.mjs` rebuilds every kind from its params
      after each edit and finds it within 0.5 pt of the moved samples.
  - `appendStrokes(ink, strokes)` adds strokes to a group as they are (a
    paste's copies, a merge's moved strokes, `from` and all), re-minting
    only an id the group already holds. It answers `{ink, ids}` as they
    landed.
  - `flipStrokes(ink, ids, {cx, cy, axis})` mirrors the selected strokes
    about `(cx, cy)` in the samples alone: `'h'` rewrites each `x` as `2cx
    − x`, `'v'` each `y` as `2cy − y`. Size, `p`, `t`, `w`, ids and
    channels stay.
    - It carries a `shape` record through the same reflection (`mapShape`:
      points and centres reflected, an angle `180° − angle` for a
      horizontal flip and `−angle` for a vertical one, re-normalised as
      `recognise` returns them).
    - It carries a `nib` as `(180 − nib) mod 180`, the same on either
      flip: an undirected angle reflects the same way about both axes.
    - It returns the same object when nothing changes. Two flips are the
      identity, a unit test pins it.
  - Every helper spreads the stroke object, so unknown keys ride along.
  - `restroke` (the partial eraser's piece) keeps every key but `shape`
    and `fill`, mints an id and writes `from`.
  - `withDefaults` fills a loaded file's omitted stroke keys
    (`STROKE_DEFAULTS`), and the helpers tolerate them anyway.
  - `upgradeV1` is the version 1 converter ("Upgrading version 1").
    `loadedInk(file)` is the drawing a fetched or journalled file becomes
    (`upgradeV1`, then `withDefaults`; null for anything that is no
    gamma-ink file).
  - `sameStrokes(a, b)` compares two stroke lists in order.
  - `inkFeatures(ink)` lists what a drawing carries that this build does
    not know (tools, brushes, keys, letters, a newer version), null when
    nothing.
  - `inkBytes` / `strokeBytes` give a group's exact upload size, cached per
    object, for the byte budget (`MAX_BYTES`, `GROUP_SPLIT_BYTES`).
  - **The calligraphy pen**: `KNOWN_BRUSHES` and `BRUSH_ORDER` (null,
    `monoline`, `brush`, `pencil`, `calligraphy`: the Style picker's order
    and Shift+[ / ]'s) know it.
    - `DEFAULT_NIB` (45), `NIB_MIN` / `NIB_MAX` (−60 / 60) and `NIB_STEP`
      (15) are a preset's range.
    - `strokeNib(nib, twist, lock) → 0..179` is a stroke's angle from its
      preset's, `(nib − twist) mod 180` rounded, the twist left out when it
      is null or `lock`.
    - `normalizeTools` keeps a calligraphy pen's `nib` (an integer from −60
      to 60, omitted at 45) and `nibLock: true` (omitted when false), and
      drops both from any other preset.
    - `toolStyle` puts the preset's angle on the style a stroke is encoded
      with (`nib`, as `strokeNib(nib, null)`). It puts `nibLock` on the
      armed tool for the layer, never on a stroke.
    - `nibOutline(samples, size, nib) → d` is the pen on the screen: a flat
      segment `size` long at the angle `nib` swept along the samples
      (already stabilised).
      - The sweep is made of closed pieces, split wherever the travel
        crosses the nib's direction (where the swept band's width passes
        through zero) and at every reversal. The nib's own segment is
        inserted at each split, so a U-turn stays filled.
      - It is unioned with the same sweep of a hairline segment `NIB_HAIR`
        (0.12) × `size` long across the nib (the hairline the floor stands
        for).
      - It is unioned with the nib's footprint (the rectangle of the two
        segments) at the first and the last sample too, so a tap leaves
        the nib's mark and the ends show its corners.
      - Every piece is wound the same way (clockwise as seen) and the path
        is filled with the nonzero rule, so overlapping pieces leave no
        hole.
      - The nib is `size` long at every pressure ("The brush table").
    - `strokePath` routes a calligraphy stroke without a `shape` to it. A
      dashed one outlines each of `dashRuns`' on-runs with it, and a dotted
      one draws its dots as round dots of `size × w` as any pressure
      stroke does.
    - A calligraphy stroke with a shape draws as an even line.
    - The server's exporters draw the same paths (`gamma/ink.py`
      `nib_outline` and `nib_shape`, "Server"; `tests/shared/inknib.json`
      pins them).
    - `strokeBounds` pads a calligraphy stroke without a shape by `size ×
      NIB_REACH / 2` at every sample, or by its drawn half-width where that
      is wider. `NIB_REACH` = hypot(1, `NIB_HAIR`): as far as the nib's
      corners reach.
    - `transformStrokes` turns a `nib` with the samples, `(nib − angle°)
      mod 180` rounded (the transform turns clockwise as seen, the nib
      counts counterclockwise). `flipStrokes` mirrors it, so neither
      changes `w`.
    - Every other edit keeps it, `restroke` included. `GEOM_KEYS` holds
      `nib`.
  - **Fills**: `CLOSED_SHAPES` (`rect`, `ellipse`, `triangle`, `polygon`)
    and `isClosedShape(stroke)`.
    - `strokePath(stroke).fill` is the closed polygon through the samples
      of a filled closed shape (absent otherwise). The layer fills it in
      `stroke.fill.color` at `stroke.fill.opacity ?? 0.25` before the
      outline, in the stroke's band.
    - `restyleStrokes(ink, ids, {fill: {color, opacity?} | null})` sets or
      clears it on the selected closed shapes only (any other stroke stays
      the same object).
- `ink/finishStroke.js` (pure): `finishStroke(samples, ctx) → {commit,
  erase, label, style, status}`, the one stage between a lifted pen and
  the committed stroke.
  - `InkLayer`'s finish and the iPad `claim.commit` both call it. The live
    canvas draws the stroke in progress with the same `widthLaw`, so the
    committed look matches the line under the pen.
  - `samples`: the stroke's decoded samples in page points, `[{x, y, p?,
    t}]`, `t` ms since the stroke's first sample. They are **already
    stabilised** (the stabilizer runs as the samples arrive, below), so
    this function sees the same points the live canvas drew.
  - `ctx`, `{style, pen, k, hold, straight, scribble, groups, keep}`,
    every key but `style` optional:
    - `style` `{tool, brush, color, size, opacity}`, the preset's look;
    - `pen`, a stylus drew it;
    - `k`, css px per pt (the layer's scale, for thresholds given in
      screen pixels);
    - `hold`, the layer's live hold result at the lift, `{kind, params} |
      null` (under the iPad host, the shape the claim kept, followed
      through the final samples);
    - `straight`, the highlighter preset's `'hold' | 'always' | 'off'`
      (`undefined` reads as `'hold'`);
    - `scribble`, whether scribble-to-erase is on;
    - `groups`, the page's groups, `[{id, ink}]`, and `keep(stroke) →
      bool`, the eraser filter, both for the scribble stage.
  - The stages, in this order, each returning as soon as it decides:
    1. **hold**: `ctx.hold` set → `commit = shapeSamples(kind, params,
       {span: last t − first t, p: mean p})` (page units, `t` from 0 over
       the span), `style = {...ctx.style, pen: false, shape: {kind,
       params}}`.
       - That is an even line in the preset's colour, width, brush and
         line, constant width whatever the brush. `widthLaw` and
         `opacityLaw` are null for a style with a `shape`, so nothing is
         baked (no Brush tapers, no Pencil shading), the brush key stays
         and the stroke is written `xyt`.
       - The `shape` key rides into `encodeStroke` as an extra.
       - A `circle` is written as `{kind: 'ellipse', params: {cx, cy, rx:
         r, ry: r, angle: 0}}`, since the file's kinds (the server's
         `SHAPE_KINDS`) have no circle. `circle` stays the recogniser's
         answer for the pill and the refit.
       - `label` is `T("ink straight line")` for a line or arrow,
         `T("ink shape")` otherwise.
       - `status` is `{text, once: false}`, its text `t("Straightened.")`
         or `t("Shape: {kind}.")`, the kind named through
         `shapeName(kind)` (a `t()` per kind).
    2. **straight highlighter**: `style.tool === 'highlighter'`, `straight
       === 'always'`, no hold result.
       - When every sample lies within `min(0.75 × size, 6)` pt of the
         chord (first to last sample) and the chord is `≥ 4 × size` long,
         `commit` is the two-sample chord, snapped horizontal when within
         4°, `t` 0 and the span.
       - Then `style = {...ctx.style, pen: false, shape: {kind: 'line',
         params: {x1, y1, x2, y2}}}`, `label` `T("ink straight line")`,
         `status` `{text: t("Straightened."), once: true}`.
       - Otherwise the stroke goes on to the stages below unchanged.
       - `'hold'` is the hold stage with lines only, which the layer
         enforces by asking `recognise` for lines alone. `'off'` disarms
         the hold timer for that preset.
    3. **scribble**: `ctx.scribble`, `style.tool === 'pen'` (any brush),
       no hold result → `isScribble(samples, {k})` and then `targets =
       scribbleTargets(samples, groups, {keep})`.
       - With at least one target the answer is `{commit: null, erase:
         targets, label: T("ink scribble erase"), style: ctx.style,
         status: null}` (the layer's `onEraseMany` raises the pill).
       - With none, or when it is not a scribble, the stroke goes on as
         ink.
    4. **decimate** (`DECIMATE_PT`, 0.1 pt) then **bakeWidths**, answering
       `{commit, erase: null, label: T("ink stroke"), style: ctx.style,
       status: null}`.
       - The brush table `BRUSHES` is the only place a width or opacity
         law lives: `{pen: {w: law}, monoline: {}, brush: {w: law over
         the whole stroke, simulate: the speed law}, pencil: {w: law, o:
         law}, calligraphy: {w: law of the travel against the nib, travel:
         true}}`. It is read through `widthLaw(style)`,
         `opacityLaw(style)`.
       - `bakeWidths(samples, style)` writes `w`, and `o` for the pencil,
         rounded to three decimals. It runs the brush's tapers over the
         samples' arc length (each at most half of it, none on a stroke of
         no length) and its speed law when the style has no pressure
         (`pen: false`, or samples without `p`).
       - A style with a `shape` has no laws, so a snapped stroke is never
         baked.
       - `brushWidths(samples, style, {live})` and `brushOpacities(samples,
         style)` are the same values as arrays. `live` is for a stroke
         still being written (the live canvas, a peer's wet stroke).
         - It leaves the end taper out and draws the start taper at its
           full length, whatever the stroke's length so far, so the line
           under the pen holds still.
       - `strokeChannels(style, samples)` is the `ch` a new stroke is
         written with:
         - `xyptw` for a pressure pen or brush, `xyptwo` for a pencil;
         - `+az` after `t` when any sample reported tilt (`xyptazwo`);
         - `xypt` for a monoline or a highlighter;
         - `xyt` for a mouse or finger stroke and for every snapped result
           (`pen: false` and a `shape`, whatever the brush), except a mouse
           brush's freehand stroke, `xytw`.
  - `restyleBrush(ink, ids, brush) → ink`: the selection's brush change. It
    lives here, not in `ink.js`, because the bake does, and `ink.js` never
    imports upward.
    - It works on every selected stroke of tool `pen` (`brush` `null` or
      `'pen'` for the plain pen, else `monoline` | `brush` | `pencil` |
      `calligraphy`; any other value is ignored).
    - It decodes the samples, strips `w` and `o`, and bakes them again for
      the new style. The bake reads the stroke's own `pen` flag, so a mouse
      stroke turned brush gets the speed law.
    - It reads the stroke's `shape` too, so a snapped stroke stays constant
      width: only its brush key changes, and a `w` or `o` an older build
      baked for it goes.
    - It re-encodes with the stroke's own channels but `w` and `o` plus
      what the new brush writes (`strokeChannels` for every stroke this
      build writes, while a letter this build does not know keeps its
      place). It writes the new `brush` key (dropped for the plain pen).
    - A stroke already in that brush whose widths and shades are within
      `BAKE_SLACK` (0.05) of the ones baked again is the same object, so
      choosing its brush again changes nothing. The bake at the lift read
      the samples before the file rounded them to 1/100 pt, which moves a
      Brush's tapers by up to about 0.02.
    - One whose key and widths disagree is baked again.
    - Highlighters are the same objects, and the same `ink` object comes
      back when nothing changed.
    - A change to `calligraphy` bakes with the stroke's own `nib`, else
      `DEFAULT_NIB`, and writes it. A change to any other brush drops
      `nib`.
  - **Calligraphy** in `BRUSHES` (`calligraphy: {w, travel: true}`): its
    law reads the sample's travel ("The brush table": the samples around
    it) and the style's `nib` (`DEFAULT_NIB` when absent). So
    `brushWidths` hands it the samples and the index.
    - `widthLaw` answers for it whether or not the stroke reads pressure
      (p = 0.5 without), so `strokeChannels` writes a mouse calligraphy
      stroke `xytw`. It answers null with a `shape`, as for every brush.
    - `tests/shared/inkwidth.json` pins it row by row (each calligraphy row
      with its `nib`).
    - The live canvas and a peer's wet stroke draw `nibOutline`, not these
      widths.
  - The result:
    - `commit`, the samples to encode (`null` for nothing);
    - `erase`, `[{gid, ids}] | null`;
    - `label`, the history entry's;
    - `style`, the object to encode with (`ctx.style` itself when nothing
      snapped);
    - `status`, `{text, once} | null`, a pill with Undo
      (`undoableInkStatus`), `once` meaning once a session.
  - The caller encodes with `encodeStroke({...style, t0, samples: commit,
    ch: strokeChannels(style, commit)})`, erases through `onEraseMany`,
    and hands `status` to `onHoldStatus`.
- `ink/shapes.js` (pure; fixture `tests/shared/inkshapes.json`, whose
  `about` pins the parameter conventions): the draw-and-hold recogniser.
  - `recognise(samples, {k, kinds}) → {kind, params} | null` over decoded
    page-pt samples `[{x, y, p?, t?}]` as the layer holds them when the
    timer fires (the still tail included).
    - `kinds` (optional) restricts the answer, `['line']` for a
      highlighter.
    - Thresholds in css px divide by `k`, and everything else is points or
      ratios.
    - Null with fewer than 8 samples, or when the stroke's extent, its
      bounding-box diagonal, is under the size floor,
      `max(MIN_EXTENT_PX / k, MIN_EXTENT_PT)` pt (24 css px, 18 pt).
    - The samples are then resampled at the diagonal / 40, and the stroke
      is **closed** when its ends lie within 10 % of the diagonal.
    - An open stroke is a **line** when its path is at most 1.08 × its
      chord and the samples' RMS distance to their least-squares line is
      at most 3 % of the chord. That is `line {x1, y1, x2, y2}` from the
      first endpoint to the last, snapped to 0°, 45° or 90° when within 5°
      by a turn about its midpoint (length kept).
    - Else an open stroke may be an **arrow**, read from distances to the
      tip, since ShortStraw cannot place corners along a head a few
      samples long:
      - The tip is the raw sample farthest from the first, and the shaft
        up to it must pass the line test. Every later sample lies within
        0.4 × the shaft of the tip.
      - The farthest on each side of the shaft line are the barbs, both
        needed and neither over 0.35 × the shaft.
      - That is `arrow {x1, y1, x2, y2, head}` (`x2, y2` the tip, `head`
        the mean barb, the shaft snapped as a line).
      - A mean barb under max(6, 0.08 × the shaft) pt, or a head whose path
        is not 1.8 to 4.5 times it (a hook, a loop), makes none.
      - Any other open stroke is null.
    - A closed stroke's corners are ShortStraw's (window 3, a straw under
      0.95 × the median, kept where the path turns 30° or more), the join
      counting as one where the path turns there too.
    - Three to eight corners make a polygon (the intersections of its
      sides' least-squares lines), kept when the samples' RMS distance to
      it is at most 3 % of the diagonal:
      - **3** → `triangle {x1, y1, x2, y2, x3, y3}` in drawing order from
        the corner nearest the first sample;
      - **4, angles 90° ± 15°** → `rect {cx, cy, w, h, angle}`, `angle` in
        [−45°, 45°) and 0 after an axis snap (within 5°), `w` the side
        along `angle`, a square (sides within 10 %) taking the mean side
        for both;
      - **5–8, convex, sides within 15 % and turns all one way** →
        `polygon {cx, cy, r, n, angle}` (circumradius, side count,
        direction of the first corner in [0, 360 / n)).
    - A closed stroke that makes no polygon goes to the inertia test
      (Xournal++):
      - With the central moments' eigenvalue ratio ≥ 0.95 and the radial
        deviation about the centroid ≤ 10 % of the mean radius it is a
        `circle {cx, cy, r}`.
      - Else, with the radial deviation about the moment ellipse ≤ 12 %, it
        is an `ellipse {cx, cy, rx, ry, angle}` (`rx ≥ ry`, `angle` in [0°,
        180°)).
      - Else it is null.
  - `shapeSamples(kind, params, {span, p}) → [{x, y, p, t}]`: the dense
    polyline a shape commits as.
    - A line is two samples. An arrow is tail → tip → barb → tip → barb,
      each leg cut into equal steps of at most 2 pt.
    - A triangle, rectangle or polygon is its corners, each side cut
      alike, closed (the last sample is the first again).
    - A circle or ellipse is max(48, ⌈perimeter / 2⌉) samples even in
      angle (Ramanujan's perimeter), closed.
    - x and y are on the 1/100 grid, `t` spread evenly from 0 to `span`
      (ms), `p` constant.
    - `gamma/ink.py` `shape_samples` gives the same x and y (the fixture's
      `samples`), so a server writer of shape records bakes the samples a
      handle drag would write.
  - `refitShape(kind, params, {pointer, from, shift}) → params`: the shape
    under a continued hold, computed from the **snap's** params every time
    (never from an already-refit set). `pointer` is the current tip and
    `from` the tip at the snap, both in page points.
    - A line or an arrow takes `pointer` as its new end (Shift snaps the
      direction to 15°).
    - A closed shape scales about its centre by |pointer − centre| / |from
      − centre| and rotates by the angle from `from` to `pointer` about the
      centre (Shift snaps the rotation to 15°). A rectangle or triangle
      keeps its proportions.
    - The result is normalised as `recognise` would return it.
    - A pure refit needs `from`: without the snap position it cannot tell
      a scale from a rotation.
  - The hold's numbers are one table for the layer and the iPad host,
    whose Swift constants mirror it. They are `HOLD_MS` 600, `HOLD_MOVE` 3
    css px (a tip that moves farther starts the timer again),
    `HOLD_MIN_MS` 250, `REFIT_PX` 6, `CANCEL_PX` 12, `CANCEL_MS` 150 and
    `PASTE_MOVE` 2.
  - `holdQualifies(samples, k) → bool`: whether a hold asks the recogniser
    (InkLayer's `holdFire`, and the iPad claim's hold).
    - The page-pt samples, at least 2, must reach the recogniser's size
      floor by their extent, the bounding-box diagonal `recognise`
      measures. Their chord would not do: a closed shape ends where it
      began.
    - The hold's other rules (`HOLD_MS` still, `HOLD_MIN_MS`) are the
      callers'.
  - `stillSince(samples) → index` (fixture `tests/shared/inkhold.json`,
    which the iPad app's XCTest runs too): the stillness anchor of `[{x,
    y}]` in css px. It is the latest sample more than `HOLD_MOVE` from the
    anchor before it, the first sample to start with.
    - A hold fires `HOLD_MS` after the anchor's sample, so the snap's time
      is that sample's `t` + `HOLD_MS`.
  - `followHold(hold, samples) → params | null` (the same fixture): the
    shape a hold under the iPad host comes to, the rule the shell runs too
    (`ShapeHold`). It works over `hold = {kind, params, from, n, tSnap}`
    and the stroke's `[{x, y, t}]`, all in the host's css px.
    - Of the samples from index `n` on, the first one more than
      `CANCEL_PX` from `from` while `t − tSnap ≤ CANCEL_MS` drops the shape
      (null).
    - Once one has been `REFIT_PX` from `from` the answer is
      `refitShape(kind, params, {pointer: the last sample, from})`, else
      `params`.
  - `shapeName(kind) → string`: the kind's translated name for the "Shape:
    circle." pill.
  - `shapeHandles(kind, params) → [{id, x, y}]`: the handles a shape held
    alone in the selection shows, none for a `curve` or a kind it does not
    know:
    - `p1` and `p2`, a line's or an arrow's ends (`x1, y1` and `x2, y2`);
    - `c0`, `c1`…, a rectangle's, a triangle's or a polygon's corners, in
      `shapeSamples`' order;
    - `rx` and `ry`, an ellipse's long- and short-axis ends.
  - `dragHandle(kind, params, id, point, {shift}) → params`: the params
    with that handle at `point` (page points), normalised as `recognise`
    returns it, on the 1/100 grid.
    - An end moves alone (Shift: in 15° steps about the other), and a
      triangle's corner alone.
    - A rectangle's corner moves with the opposite one fixed and the angle
      kept. A polygon's corner sets its radius and its turn (still
      regular).
    - `rx` sets the long axis's length and direction, `ry` the short one's
      length.
  - `reshapeStroke(stroke, params) → stroke`: the stroke written again from
    them, `shapeSamples(kind, params, {span: its own first-to-last t, p:
    its mean p})` encoded with its own `ch`. Its `shape` is the new params,
    and every other key is kept (id, look, `t0`, `fill`, extras).
    - It is the XY-only rewrite a handle drag commits, so params and
      samples agree as after every other edit.
    - `reshapeInk(ink, id, params) → ink` is it on the group's stroke of
      that id (the same group when it has no such stroke with a shape). It
      is what the selection's `reshape` action applies
      (`ink/useInkSelection.js`).
  - `snapEnd(from, to)`: the Shift 15° end snap that `refitShape`,
    `dragHandle` and `shapeTool.js` `toolShape` share.
- `ink/shapeTool.js` (pure; `tests/shared/inkshapes.json` `tool`, whose
  `about` is the normative statement, `frontend/tests/shapeTool.test.mjs`):
  the Shapes tool.
  - `SHAPE_TOOL_KINDS` (`line`, `arrow`, `rect`, `ellipse`) are the values
    `app/prefDefs.js` allows the account's `inkShapeKind`, and
    `SHAPE_MIN_PX` is 4.
  - `toolShape(kind, from, to, {shift, k}) → {kind, params} | null` is the
    shape a drag from `from` to `to` (page points, at `k` css px per pt)
    draws, every number on the 1/100 grid:
    - null under 4 css px;
    - a line or an arrow from `from` to `to` (no `head`), Shift turning it
      to the nearest 15°;
    - a rectangle `{cx, cy, w, h, angle: 0}` spanning the two;
    - an ellipse inscribed in that box with `rx ≥ ry` (angle 0, or 90 for
      a tall box);
    - with Shift, either of the last two takes the square box of the
      larger side, laid toward `to`.
- `ink/ruler.js` (pure; `frontend/tests/ruler.test.mjs`): the ruler.
  - `RULER_PX` (36, the band's width in css px), `RULER_SNAP_PX` (12, the
    reach outside each edge), `RULER_MIN` (36 pt, its shortest length),
    `RULER_STEP` (15°, the knob's snap).
  - A ruler is `{surface, x, y, angle, length, snap}`: its middle in page
    points, its angle in degrees counterclockwise as seen in [0, 180) (its
    direction the page vector `(cos angle, −sin angle)`), its length in
    page points and whether the knob turns it in 15° steps.
  - `defaultRuler(surface, visible) → ruler` lies level across the middle
    of `visible`, the part of the surface on the screen in page points,
    four fifths of its width.
  - `rulerDir(ruler)`, `rulerEnds(ruler)` and `rulerEdges(ruler, k) →
    [{origin, dir}, {origin, dir}]` (the edges' lines, `RULER_PX / 2 / k`
    pt either side of its middle line).
  - `snapRail(ruler, point, k, {band}) → {origin, dir} | null` is the edge
    a stroke that starts at `point` is drawn along. It is the nearer one,
    when the point lies on the band (`band`: the pointer may write there,
    a pen) or within `RULER_SNAP_PX / k` pt outside an edge, and between
    the ruler's ends (half a css px of slack).
  - `onRail(sample, rail)` is the sample's point projected onto the rail's
    line, every other key kept.
  - `railLine(samples, rail) → {x1, y1, x2, y2}` is the line between the
    two farthest projections, from the end nearer the first sample's, on
    the 1/100 grid.
  - `turnRuler(ruler, deg, {snap})` turns it about its middle.
    `snapAngle(deg, step = 15)` snaps into [0, 180), and `stepAngle(deg,
    ±1)` is the next 15° either way (the knob's Shift + arrow).
  - `moveRuler(ruler, dx, dy)` moves it.
  - `pinchRuler(ruler, from, to, {snap})` is two fingers: the ruler is
    turned by the change of the angle between them and carried with their
    midpoint, turning about it, the fold at 180° kept away from the
    middle's turn.
  - `stretchRuler(ruler, end, point)` takes that end to the point's
    projection, the other staying, at least `RULER_MIN`.
  - `rulerTicks(ruler, units, k)` is what the band draws: in pt a tick
    every 10, longer every 50, numbered every 100, and in cm every
    millimetre, longer every 5, numbered every centimetre. A rank closer
    than 3 css px is left out, and the numbers when closer than 24.
  - `stampLine(ruler, look, {t0})` is Stamp line's stroke: the middle line
    from the start end to the other in a preset's look through
    `finishStroke`'s hold stage, both samples at t 0, encoded.
  - `hostSnap(rail, angle, {left, top, k})` is the claim's `snap` in
    viewport css px.
- `ink/zoomWindow.js` (pure; `frontend/tests/zoomWindow.test.mjs`): the
  zoom window's rules over `useInkZoom`'s `inkZoom` and the page's
  `{width, height}` in points.
  - `inkZoom` is `{page, x, y, w, h, mag, line, x0}`:
    - `page`, the PDF page's number, and `x`, `y`, `w`, `h`, the window's
      top-left, width and height in page points;
    - `mag`, the magnification in the panel in css px per pt (2 to 4),
      `line`, the line height in points, and `x0`, where the line began.
  - The constants:
    - `ZOOM_MAGS` ([2, 3, 4]) and `ZOOM_MAG` (3; `ZOOM_MAG_MIN` 2 to
      `ZOOM_MAG_MAX` 4);
    - `ZOOM_LINE` (24 pt; `ZOOM_LINE_MIN` 8 to `ZOOM_LINE_MAX` 96 in steps
      of `ZOOM_LINE_STEP` 2);
    - `ZOOM_HEIGHT` (200 css px; `ZOOM_HEIGHT_MIN` 160 to `ZOOM_HEIGHT_MAX`
      320), the default and range `app/prefDefs.js` gives
      `inkZoomHeight`;
    - `ZOOM_CHROME` (45 css px: what the panel's top border, grip and bar
      take of its height);
    - `ADVANCE_AT` (0.8) and `ADVANCE_BY` (0.6).
  - `openWindow(page, size, visible, panel)`: `visible` is the part of the
    page on the screen in page points, `panel` the panel's view `{width,
    height}` in css px. `w` and `h` come from the view at `ZOOM_MAG`, and
    the window goes at the left of the visible part, a twentieth of the
    page's width in, its middle at the visible part's, `x0` its `x`.
  - `sized(win, panel, size)` sets `w` and `h` again, the view's css px
    over `mag`, when the view's size changes, the top-left kept.
  - `clampWindow(win, size)` keeps it inside its page. A window wider than
    its page keeps its left edge on it.
  - `advance(win, box, size)` runs after a stroke whose box reached past
    `x + ADVANCE_AT × w`. It moves to `x + ADVANCE_BY × w`, or, when that
    would pass the page's right edge, to `x0` and one line down.
  - `newLine` goes to `x0`, one line down (at the page's bottom nothing
    moves). `nextLine` and `prevLine` go one line down or up, `x` kept.
  - `moveWindow(win, {x, y}, size)` puts the top-left there, `x0` with it:
    the page's drag band.
  - `moveTo(win, page, point, size)` centres it on a point of `page`,
    which may be another one, `x0` there: a tap.
  - `setMag(win, mag, panel, size)` is held to 2…4, `w` and `h` from the
    view (the window's own `w × mag` without one), the left edge and the
    vertical middle kept.
  - `magForWidth(win, width)` is the magnification that makes the window
    `width` points wide in the same panel: the page's resize corner.
  - `setLine(win, line)` sets the line height.
  - `revealDelta(rect, view, pad)` is how far the viewer scrolls to bring
    the window's box into view, only as far as it must.
  - Every answer is clamped to its page, and one that changes nothing is
    the window itself.
- `ink/gestures.js` (pure; fixture `tests/shared/inkscribble.json`): the
  scribble.
  - `isScribble(samples, {k, durationMs}) → bool` over decoded page-pt
    samples (`durationMs` defaults to last `t` − first `t`). It is true
    when all of these hold:
    - at least **4 reversals along the PCA major axis** of the samples,
      each with a **turn over 120°** (the angle between the path direction
      4 css px before the reversal and 4 css px after it). A reversal is an
      extremum of the samples' projection on that axis whose excursion on
      both sides is at least 4 css px, so jitter never counts.
    - the path lengths between consecutive reversals (the first and last
      partial runs excluded) **within 2× of each other**;
    - the path **≥ 2.5 × the bounding-box diagonal**;
    - the box's height **≤ 1.5 × its width or ≤ 40 css px**;
    - duration **≤ 2000 ms**;
    - mean speed (path × k / duration) **≥ 150 css px/s**.
  - A cursive word advances along its major axis and hardly reverses on
    it, and hatching's major axis runs across the lines. Both stay ink.
  - `scribbleTargets(scribble, groups, {keep}) → [{gid, ids}]`, `groups`
    the page's `[{id, ink}]` in order.
    - A stroke is a target when its `strokeBounds` box lies at least 70 %
      by area inside the scribble's sample bounding box, its `tool` is not
      `highlighter`, its box's diagonal is at most 3 × the scribble's and
      `keep(stroke)` is true. `keep` defaults to accept-all.
    - The answer lists groups in order with ids in file order and leaves
      out groups with no target.
- `ink/stabilizer.js` (pure): `makeStabilizer(level, {k}) → {push(sample)
  → sample | null, finish(sample) → sample[]}`, the lazy brush behind a
  preset's `stabilize`. Nothing here reads the stroke's style.
  - `R = [0, 3, 6, 10, 16][level]` css px, divided by `k` to points. Level
    0 (or an unknown level) passes every sample through (`push` returns
    it, `finish` returns `[sample]`).
  - The brush starts at the first sample (returned as it is).
  - `push(sample)`: when the pointer is within `R` of the brush nothing is
    emitted (null). Otherwise the brush moves toward the pointer by the
    distance beyond `R`, and the emitted sample is the brush position with
    the pointer's `p` and `t` (and `a`, `z` when present).
  - The **cusp rule**: when the pointer's direction (from the previous
    pointer position) turns more than 90° against the direction the brush
    last moved while the string is taut, the previous pointer position is
    the tip of a cusp.
    - Once the pointer has retreated at least `R / 2` from it, measured
      along the brush's last move, the brush jumps to the tip and that
      point is emitted. So a corner stays a corner.
    - A smaller retreat is tremor and emits nothing.
    - A pointer passing the tip again forgets it, and so does one that
      leaves the tip sideways by more than `R`: a shake across the stroke,
      which the string smooths rather than copies.
  - `finish(sample)` (the lift) returns the catch-up: a pending tip first,
    then at most 8 samples along the quadratic from the brush to the lift
    point (control point: the brush plus half its last move).
    - They are spaced about `R / 2`, `p` the lift's, `t` spread from the
      last emitted `t` to the lift's, ending with the lift point exactly.
- `ink/fingerChords.js` (pure): `makeChordDetector({onChord, mode, now}) →
  {down(e), up(e), cancel(e), move(e), palm(), setMode(mode), reset()}`,
  the two- and three-finger undo chords.
  - `e` is any object with `pointerId`, `pointerType`, `clientX`,
    `clientY` and `timeStamp`. `now()` is only for tests: the events' own
    `timeStamp` is the clock.
  - `mode` is one of `CHORD_MODES`, `'off' | 'tap' | 'double'`, the values
    `app/prefDefs.js` allows `inkFingerUndo`.
  - A **chord** is N = 2 or 3 `touch` contacts whose downs lie within 80
    ms of the first. None of them `move`s more than 10 css px from its
    down, and all are lifted by `up` within 250 ms of the first down.
    - A `cancel` of any contact voids the chord in progress. A
      `pointercancel` is never a lift, so a pinch, which cancels both
      touches, never undoes.
    - A fourth contact or a late one (after 80 ms) voids it.
    - A chord while a `pen` contact is down (seen by `down` and not yet
      lifted or cancelled) is a palm and ignored.
  - N = 2 fires `onChord('undo')`, N = 3 `onChord('redo')`.
  - In `'tap'` mode a chord fires as its last contact lifts.
  - In `'double'` mode it fires when a second chord of the same N
    completes within 400 ms of the first's last lift with its centroid
    within 30 css px of the first's (`DOUBLE_MS` / `DOUBLE_SLOP`, the
    values `shared/ui/press.js` uses).
  - `'off'` fires nothing but still tracks contacts, so `setMode`
    mid-gesture is safe.
  - `palm()` voids the chord in progress, for a pen the detector never saw
    (the iPad host owns the Pencil: `InkLayer`'s `penBusy()`). `reset()`
    forgets everything (window blur).
- `ink/stripKeys.js` (pure): `stripKey(e, state) → action | null`, the
  strip's key table (matched by `e.code`). The capture-phase handler in
  `useStripKeys` dispatches it for the keydown and, for `E` alone, the
  keyup.
  - What each key does, and which chords are left alone, is
    [hotkeys.md](hotkeys.md#widget-keys-the-markup-strip-and-a-text-box)
    "Widget keys". This is the table's contract.
  - `state` is `{tools: the visible presets, tool, hasSelection, boxUndo,
    hasBox, optionsOpen, eraserDown, ruler, zoom, onControl}`:
    - `eraserDown`, the timeStamp of the `E` keydown still held, or null,
      so the keyup's `held` is pure arithmetic;
    - `ruler` and `zoom`, whether they are on;
    - `onControl`, the key's target is a button, a link, a menu row or a
      comment thread (a margin card, or the thread popover).
  - The actions:
    - `{kind: "pick", id}` for `1`–`9` (the visible list), `P` / `H`
      (forward; with Shift backward, wrapping), `L`, `T`, `V`.
    - `{kind: "width", step: ±1}` for `BracketLeft` / `BracketRight`: the
      handler steps the armed preset through `sizesFor(kind)` and says
      "{name} · {size} pt." on the pill, where `name` is the preset's
      colour and kind in words.
    - `{kind: "brush", step: ±1}` for the same keys with Shift: the
      handler steps an armed pen preset through `BRUSH_ORDER` (`null` for
      Pen, `monoline`, `brush`, `pencil`, `calligraphy`), wrapping, and
      raises the same pill, whose `name` says the style ("Blue brush pen ·
      2 pt."). An armed highlighter or no preset ignores it.
    - `{kind: "eraser", phase: "down" | "up", held}` for `E`: `down` on a
      non-repeat keydown, `up` on the keyup with `held` the ms since the
      down, so the handler decides tap or hold ("Hold-E" under
      `ink/useStripKeys.js`).
    - `{kind: "toggle", what: "ruler" | "shape" | "zoom"}` for `R`, `S` and
      `Z`.
    - `{kind: "newLine", prevent: true}` for Enter (and the keypad's)
      while `zoom` and neither `onControl` nor `hasBox`.
    - `{kind: "picture"}` for `I`: the strip's Picture button. `P` stays
      the next pen.
    - Escape, in order: `hasBox` leaves it to the box or the picture (null),
      `hasSelection` → `deselect`, `optionsOpen` → `closeOptions`, the
      Shapes tool armed (`tool === "shape"`) → `{kind: "off", what:
      "shape"}`, `ruler` → `{kind: "off", what: "ruler"}`, `zoom` →
      `{kind: "off", what: "zoom"}`, else `close`.
    - `delete` and `undo`.
  - `stepWidth(tools, id, step)` and `stepBrush(tools, id, step)` are what
    the handler does with `width` and `brush` to the armed preset of the
    account's list. They answer `{preset, size, next}`: the preset and the
    size the pill names, and the list to write.
    - At either end of the sizes `next` is null and the pill still shows.
      With no preset armed (no pen, for a style) the answer is null.
- `ink/pencilActions.js` (pure): `pencilAction(message, prefs, state)`,
  the Pencil's double tap and squeeze as `usePencil` applies them.
  - `backTool(prefs, state)` is the tool to go back to: the one armed
    before, when it is still on the strip and not the current one, else
    the stylus pen. The gestures' *Switch to last tool* and
    `useInkStrip`'s `inkBack` both read it.
  - `stylusPen(tools, pen)` is that pen. It is `pen`, the last pen armed,
    while that is a pen preset on the strip, else the strip's first pen,
    else its first preset (undefined for an empty strip).
  - `useInkStrip`'s `inkPenPreset` is it, and opening the strip
    (`openInkStrip`) arms it. `backTool` and the `'options'` gesture with
    the hand armed fall back to it.
  - `RESERVED_TOOLS` (`"eraser"`, `"select"`, `"text"`, `"shape"`) are the
    strip's tools that are no preset. They count as on the strip, and
    `useInkStrip` reads the list too.
  - `message`: `{gesture: 'tap' | 'squeeze', action, phase?: 'began' |
    'ended' | 'cancelled'}`: the host's `pencil`, and its `squeeze` as the
    gesture `squeeze`. A bare action string reads as a tap.
    - A squeeze in any other phase (a later shell's `changed`) answers
      null.
  - `prefs`: `{tools, pen, tap, squeeze}`: the presets on the strip, the
    last pen armed (`useInkStrip`'s `inkUi.pen`, which `stylusPen` reads),
    and this browser's `inkPencilTap` and `inkPencilSqueeze`.
    - `'system'` (the default) maps the system's action: `switchEraser` →
      `'eraser'`, `switchPrevious` → `'previous'`, `showColorPalette` and
      `showInkAttributes` → `'options'`, anything else → `'none'`.
    - `'eraser'` toggles the eraser against `backTool`, and `'previous'`
      picks `backTool`.
    - `'options'` opens the armed tool's row (with the hand armed, the
      stylus pen's), `'undo'` undoes and `'none'` does nothing.
    - A squeeze also takes `'holdEraser'` and `'holdPrevious'`.
  - `state`: `{tool, previous, options, busy, squeeze, now}`, `busy` a
    stroke or a claim in progress (`InkLayer.jsx` `inkBusy()`), `squeeze`
    the squeeze `usePencil` holds open (`{since, ignored, hold: {tool,
    used} | null}`, or null), `now` in ms.
  - The answer is one of these, or null:
    - `{pick: id}`: arm it (null is the hand);
    - `{ui: patch}`: the strip's state;
    - `{undo: true}`;
    - `{began: {ignored, pick?}}`: a squeeze started. `pick`, only for a
      hold choice not ignored, is the tool to arm while held:
      - what the choice's toggle would arm, the eraser, or `backTool` with
        the eraser armed already, so a short squeeze toggles back;
      - `backTool` for `'holdPrevious'`.
    - `{release: {pick}}`: a held squeeze ended or was cancelled, back to
      `pick`, the tool armed when it began (null the hand);
    - `{release: {stay: true}}`: after a squeeze under `SQUEEZE_TAP_MS`,
      300, that neither erased nor drew, the tool it armed stays.
  - A message without `phase` acts at once, a hold choice as its toggle
    (`'eraser'`, `'previous'`).
  - `began` answers for every choice, so its end can tell. `ended` acts
    for the other choices only after a `began` that was not ignored, and
    `cancelled` acts only for a hold.
  - While `busy` nothing acts but a hold's end, and a `began` comes back
    `ignored`.
  - `pencilStep(message, prefs, state, now)` is `usePencil`'s step for one
    message, with the squeeze it holds open. It answers `{squeeze, run}`,
    the squeeze after the message and what to run, in order (`["pick",
    id]`, `["haptic", "tool"]`, `["restore", selection]`, `["undo"]`,
    `["ui", patch]`).
    - Its `state` adds `readOnly`, `takesInk` and `selection` to
      `pencilAction`'s: where only a held squeeze's end acts, and the
      lasso's selection a hold keeps and its end restores.
- The replay (`ink.js`, pure;
  [recording.md](recording.md) "One clock for every replay"):
  - `strokeWalls(stroke)` is each sample's wall-clock ms (`t0` plus its
    `t`), null for a stroke without timing. `inkWallSpan(ink)` is the
    stretch a drawing's timed strokes cover.
  - `inkTimeline(ink)` puts the strokes in the order they were written.
    - A stroke starts at `t0` plus its first sample's `t`, and the file's
      order breaks ties.
    - A stroke with no timing counts as written right after the stroke
      before it in the file, its samples spread over 300 ms.
    - Each sample gets a replay time: gaps between strokes and inside one
      are cut to `REPLAY_PAUSE` (400 ms), and the whole replay is scaled
      down to `REPLAY_MAX` (15 s) when longer.
    - Its `times` map (stroke id → the samples' replay times) is the
      compressed clock's table.
  - `inkUntil(ink, at, times)` is the drawing at an instant: the strokes
    begun by then in the order begun, the one being written cut short, the
    ids not yet reached.
    - A prefix of a stroke's delta-coded `pts` is its first samples, so
      that cut is a slice.
    - It runs on a clock's table, or on the strokes' own wall clock
      without one. `inkAtTime(ink, timeline, t)` is `inkUntil` on the
      timeline.
  - `replay/playback.js` plays one replay at a time on animation frames,
    on a clock (`replay/clock.js compressedClock` for a group's or a
    sheet's own replay).
  - `replay/useFrame.js useGroupFrame(groups, surface)` gives a layer or a
    card the state only while one of its own groups or its sheet takes
    part, so only they re-render. `InkLayer.jsx ReplayStrokes` draws the
    group as it stood.
  - `InkReplayButton` starts and stops it. An edit to the group
    (`applyInk`) or leaving the page ends it.
- `ink/inkStore.js`: files by URL, and per-block **drafts**, the strokes as
  edited here, ahead of upload.
  - A draft wins over the block's file until the upload replaces `ink_url`
    with the draft's. A remote `ink_url` change on a block with nothing
    unsaved drops the draft.
  - `loadInk` makes what it fetches a drawing through `ink.js` `loadedInk`
    (`upgradeV1`, then `withDefaults`). So a version 1 file (a copy that
    has not run the upgrade step) renders and, once edited, uploads as
    version 2.
  - A draft keeps its base's stroke ids (`baseIds`), which the save sends
    as `base_ids`.
  - A file loaded again for a block (the one a peer's save names, the
    merged file a save comes back with) goes through `adoptStrokes(ink,
    known)`, `known` the drawing on screen.
    - By stroke id, a stroke equal to the one on screen is that object, and
      one with equal samples keeps that `pts` array. So the outline caches
      survive the reload (`inkFor` hands `loadInk(url, known)` the drawing
      it last showed for the block).
  - `inkShown(block)` is what a layer and a card draw: `inkFor`'s drawing,
    or the one shown last while the block's new file is on its way, so a
    peer's save never blinks a group out. Edits read `inkFor`, which waits
    for the file they change.
  - **The journal** (`ink/inkJournal.js`) is the IndexedDB database
    `gamma-ink-journal`, store `drafts`, key `<workspace>/<block
    id>/<tab>`.
    - Its value is `{bytes, base, baseIds, at, tab, sent}`:
      - `bytes`, the dirty draft's canonical `serializeInk` bytes;
      - `base` and `baseIds`, the file it was edited from and that file's
        ids;
      - `at`, when, and `tab`, the tab that wrote it;
      - `sent`, the uploads whose block `PUT` that tab sent without an
        answer yet, oldest first.
  - `inkStore` writes a draft's entry:
    - at once when it changes. A write put off past a change would leave
      an older drawing for a close that writes nothing more (WebKit), which
      a restore takes for unsaved work;
    - during an eraser pass, which changes a group every frame, 300 ms
      after the pass's last frame. A WebKit close in those 300 ms leaves
      the drawing from before the pass;
    - at once when a `PUT` goes (`markSending`, awaited, for half a second
      at most, before the `PUT` of a flush the page will live past);
    - on `pagehide` and on going hidden (`journalAll`, "Fences").
  - The database opens as the page loads (`inkJournal.openDb`). A write's
    transaction then starts in the task of the change that makes it, ahead
    of the render the change causes, and is committed as it is made.
  - A block this tab never wrote an entry for gets no delete (a new
    group's `dropDraft`), so its first write waits behind none.
  - It takes a `PUT` off `sent` when its answer is an error (`markUnsent`:
    it did not land).
  - It deletes the entry when the upload lands (`markSaved` with nothing
    drawn meanwhile, which also answers every `PUT` sent before it;
    `markDeleted`) or the block starts over (`dropDraft`).
  - So an entry that outlives its tab is handwriting a flush never landed,
    or one whose `PUT` may have landed with its answer lost (a `pagehide`
    flush, a tab frozen in the background).
  - The key names the tab, so two tabs drawing in one group, or a tab
    drawing in a group before the restore of a closed tab's entry ran,
    never write over each other. A tab holds a Web Lock named after itself
    (`gamma-ink-tab-<tab>`) while it lives.
  - The workspace's entries are read once, at the first block shown,
    through a key range on the workspace's prefix (no other workspace's
    entry is loaded).
    - One older than 7 days is deleted as it is read, so an entry whose
      block never comes back goes with time.
    - A block with entries is restored the first time it is shown, one
      entry a visit. It is not restored while this tab has a draft of it,
      whose entries wait for the next visit.
    - Nor is an entry restored when it is this tab's own, or when its
      tab's lock is still held: that tab is open and saves it itself.
  - What the entry's change counts from is the newest of its `sent`
    uploads that the server has, else the entry's `base`. That `PUT` may
    have landed, so what it carried is the server's, to keep or delete.
    - A block that names no file took none of those `PUT`s: one that
      lands names a file, and nothing names none again. The entry then
      counts from its own base.
    - The upload alone would not tell: a flush the tab did not live
      through may have stored the upload and never sent the `PUT`.
    - Within the journal's 7 days a stored upload is never purged, the
      unreferenced ones being kept 30 ([user_db.md](user_db.md) "Stored
      files").
    - The side this errs on is the server's. A `PUT` that left after its
      upload landed and never landed itself, its connection gone under it,
      counts as landed too, so what only it carried is not brought back.
  - `restoredInk(entry, {url, current, base, baseUrl, lost})` decides,
    pure.
    - When the file it counts from is the block's current `ink_url`, the
      entry is the drawing as it is.
    - When the block names another file (someone saved meanwhile) and that
      file still loads, it is `mergeInk(base, entry, current)` less two
      things: every stroke `base` had that `current` lacks, and the pieces
      cut from one. mergeInk lets an edit beat a delete, which suits two
      writers at work, not a tab that is gone.
    - It is nothing, and the entry is dropped, when that differs in no
      stroke from the current file, when the base is gone (a 404), or when
      the entry is older than 7 days.
    - So a journal never brings back a stroke the server has deleted since
      the closing tab last reached it, unless the browser lost the
      journal's own last write as the page went.
  - One block file that is gone is the entry's own.
    - The pagehide flush sends the block's `PUT` beside the upload, naming
      the file by its hash ("Fences"). A `PUT` that landed while the upload
      did not leaves the block naming a file the server never got.
    - A group past handwriting's keepalive room goes as plain fetches, and
      the closing page may cut off the upload and not the `PUT`.
    - When the block's file is a 404 and its name is one of the entry's
      `sent` or the hash of the entry's bytes (`lostUpload`), the entry is
      that drawing, restored whole (`lost`). Its save uploads it again.
    - A base or block file that fails to load otherwise leaves the entry
      for the next visit.
  - A block whose `ink_url` changes while its restore runs (inkStore keeps
    the URL each block was last asked for with) is restored again against
    the new file. So *Discard* reverts to the file the drawing was laid
    over.
  - A restored drawing becomes the block's dirty draft, based on the file
    the block names at that moment, flushed like any edit. Its entry
    becomes this tab's in the same write that deletes the closed tab's
    (`moveEntry`).
    - It is reported once through `onInkRestored(fn)` → `fn({id, ink,
      prior, ids})`, queued until `useInkDocument.js` listens. That owner
      turns it into the pill "Unsaved handwriting from a previous visit
      was restored." with *Discard*.
    - *Discard* takes the restored change back as one `applyInk` entry,
      "ink restore discarded", so it is undoable in turn. It applies
      `mergeInk(ink, prior, now)`, the restored drawing to the file it was
      laid over, onto the group as it stands, as an undo does.
  - Every IndexedDB call is wrapped: a browser without it, or a private
    window, loses only the journal. `frontend/tests/inkJournal.test.mjs`
    runs it on an in-memory IndexedDB.
- `ink/inkFlush.js` (pure): what `flushInk` sends for each dirty group,
  and which of it goes with `keepalive` ("Fences").
  `frontend/tests/inkFlush.test.mjs` pins it.
  - `planFlush(groups, budget) → [{id, send, keepalive}]` takes the groups
    in the flush's order, each `{id, remove, uploadBytes, putBytes,
    beside, inFlight}`:
    - `remove`, a group erased empty with nothing under it (a `DELETE`, no
      body);
    - `uploadBytes` and `putBytes`, the UTF-8 bytes of its upload's body
      and of its block `PUT`'s (sized with the upload's name, or with
      `inkStore.UPLOAD_NAME_CHARS` while it is not named yet);
    - `beside`, its `PUT` may go beside the upload (`pagehide`, the upload
      named, no merge pending);
    - `inFlight`, `'upload'`, `'put'` or `'delete'` when a keepalive
      request of this drawing is on its way from an earlier flush.
  - `send` is one of:
    - `'delete'`;
    - `'both'`, the upload and the `PUT`: side by side when `beside`, else
      the `PUT` after the upload's answer;
    - `'put'`, the `PUT` alone, beside the upload on its way;
    - null: its `DELETE` or its `PUT` is on its way, or its upload is and
      the `PUT` cannot go beside it.
  - `keepalive` goes to each group, in order, whose bytes fit in what is
    left of `budget`. The bytes are `uploadBytes + putBytes`, `putBytes`
    for a `'put'`, and 0 for a `DELETE`, which fits even a room of 0.
  - The saver passes as `budget` the room the page's keepalive ledger
    leaves handwriting (`shared/lib/keepalive.js` `available('low')`,
    never below 0, "Fences"). A flush the page will live past passes
    -Infinity, which asks for none.
  - The saver sends each request through the ledger (`apiJson`'s
    `{keepalive: 'low'}`), asking again when it starts. A `PUT` waiting
    for its upload's answer may find the room gone and go plain.
  - It holds by drawing, until each settles, the upload, the `PUT` and the
    `DELETE` it has on their way with keepalive (`inkFlightRef`).
  - A group that does not fit goes as plain fetches, and a smaller one
    after it may still fit.
  - The bytes are the ledger's `bodyBytes` ([collab.md](collab.md)
    "Keepalive").
- `ink/InkLayer.jsx`: `InkLayer` is the retained SVG plus a
  `desynchronized` canvas for the stroke in progress. There is one per
  surface (a PDF page, a sheet in the notebook view, a sheet among the
  notes), and it draws after the highlights and the text boxes.
  - The SVG draws every group's band-0 strokes first, then every group's
    band-1 strokes (two `<g data-ink-id>` per group at most). `Strokes`
    alone, as in the notes card, orders one drawing by band.
  - A drawing `inkFeatures` finds something in is reported once
    (`onInkFeatures`). App puts one pill per page visit on it: "Some
    handwriting here comes from a newer Gamma and is drawn plainly; update
    to see it as drawn".
  - `InkSelectionMenu` is a portalled `ContextMenu` (its controls sit
    outside the page's pointer listeners).
    - A press in the page's box or in the zoom window's panel leaves it
      open (its `ignoreRef`), except one on a comment card in the page's
      box (`BESIDE_PAGE`). Any other press closes it.
    - `useSelectionMenuAnchor` gives it the selection's edges and the band
      it stays in, and the menu, from its own measured height, picks above
      or below before it is painted
      ([ui-design.md](ui-design.md#menus-and-popups-open-in-place)). A
      text box's menu and a picture's are placed the same way.
    - Its first row is fixed: Color, Width, Style (`ctlBtn`, `BrushIcon`,
      aria-label and title "Style", `aria-expanded`, `modeActive` while
      its row shows), Copy, Duplicate, More (`ctlBtn`, `MoreIcon`,
      aria-label "More", title "More edits", `aria-expanded`), Delete.
    - Color opens the `.inkEditOptions` row "Selected ink color":
      `ColorChoices` on `pickerColors(palette, …)`, the highlighter's
      colours when every selected stroke is a highlighter and the pen's
      otherwise.
      - Its `color` is the colour every selected stroke shares and its
        `alpha` the opacity they share, each left out when they differ.
      - It calls `onAction("style", {color, opacity})`: a swatch sends its
        own alpha, the custom colour the shared opacity, and with none
        shared each stroke keeps its own.
      - On a selection of more than one tool (`kinds`: pens and
        highlighters) it sends the colour alone, so each stroke keeps its
        opacity.
    - Duplicate calls `onAction("duplicate", duplicateOffset(surface))`,
      `surface` being the menu's prop for the page or sheet the selection
      is on (`ink/surfaceScale.js` below).
    - Style opens the `.inkEditOptions` row with aria-label "Selected ink
      style" (`options === "style"`, one of the three option rows).
      - That row holds the `InkStylePicker` (`ink/InkStylePicker.jsx`)
        when the selection holds a pen stroke. Its button names the brush
        every selected pen shares, else "Mixed" (aria-label "Style:
        Mixed", title "Pen style").
      - Its menu, `InkStyleMenu`, the same five rows, is the selection
        menu's own `useMenuTrigger()`, opening on the press under the
        picker, outside the `InkTooltips` host. It is a `ContextMenu`
        rendered inside the selection's, so a press in it never dismisses
        the selection menu and Escape closes it first
        ([ui-design.md](ui-design.md#menus-and-submenus)).
      - It calls `onAction("style", {brush})` with `null` | `'monoline'` |
        `'brush'` | `'pencil'` | `'calligraphy'`.
      - Then come the Line trio (`role="group"`, aria-label "Line",
        `aria-pressed` on a pill only when every selected stroke's `dash`
        matches it, calling `onAction("style", {dash})` with `null` |
        `'dashed'` | `'dotted'`), 32 px targets like the Color row's.
      - When the selection holds a closed shape (`isClosedShape`), the
        Fill group follows (`.inkFillChoices`, `role="group"`, aria-label
        "Fill", after a paint bucket glyph).
      - The Fill group is a *None* pill, aria-label "No fill", pressed when
        no selected closed shape has a fill, then `ColorChoices` on the
        highlighter picker's colours (`pickerColors(palette,
        "highlighter")`). A swatch is pressed when every closed shape is
        filled with it, and they call `onAction("style", {fill:
        {color}})` or `{fill: null}`.
    - More shows the `.inkEditOptions.inkEditMore` row (its `aria-label`
      "More edits"): Cut, Flip horizontal (`FlipHIcon`), Flip vertical
      (`FlipVIcon`), New note (`NotebookPenIcon`), Select note, Show note
      and Transcribe.
      - Transcribe is `SparklesIcon`, aria-label "Transcribe", title
        "Transcribe with AI: the selected handwriting as text in the
        chat", `onAction("transcribe")`.
      - Merge into note (`MergeIcon`) takes New note's place when
        `canMerge`: the selected ids are exactly one group's strokes and
        the surface has another group, which the layer works out from
        `groups`.
      - The row holds seven buttons either way. Whether it is open is a
        module-level flag, so it lasts the session.
    - The Color, Width and Style rows render under the open rows, one at a
      time.
    - Copy, Cut and Duplicate take their title's chord from `ink.keys` on
      the context (`"Copy · Ctrl+C"`; the label alone when unbound).
    - `usePalette` is called before the early return, so the hooks' order
      never changes.
    - The buttons dispatch `onAction(kind, value)` with `copy`, `cut`,
      `flip` `{axis: 'h' | 'v'}`, `split`, `merge`, `transcribe` and, from
      the shape handles, `reshape` `{id, params}`, beside the other kinds.
  - `InkTransformHandles` are the resize/rotate buttons. It renders
    `markup/TransformHandles.jsx` `TransformHandles` with the lasso's
    props, and `InkShapeHandles` beside them. A selected picture uses
    `TransformHandles` too ([pictures.md](pictures.md#client)). `pct` and
    `arrowSign`, which `InkLayer` reads for its other controls, live there.
    - The lasso passes `{wrapRef, box, width, height, positions,
      maxScale, onPreview, onCommit}`. For it a drag previews `{cx, cy,
      angle, scale}` and the release commits it, Shift snaps the turn to
      15°, the scale stays within 0.1 … `maxScale`, and a focused handle's
      arrow keys scale by 1.1 or turn by 15°.
    - A picture adds `turn` (both buttons turned with its frame, each
      about its corner), `labels` (each button's name and title),
      `className` (`pictureHandle`), `valueFor(raw)` (the picture reads
      the press itself) and `onStep(mode, sign)` (its own arrow keys).
    - Escape during a drag is taken on the window in the capture phase
      and goes no further: it takes back the drag, and a picture stays
      selected (its own Escape, a listener on the document, never sees the
      key). A blur ends the drag the same way, and handles unmounted
      mid-drag call `onPreview(null)`.
  - `InkTooltips` shows a button's title on hover or focus for the strip
    and the menus, since native titles are unreliable under Pencil hover.
    - A press takes the tooltip away and keeps it away while the pointer
      stays on that button. A button with `aria-expanded="true"` gets none,
      so no tooltip flickers over the menu the press just opened.
    - A menu such a button opens belongs outside the `InkTooltips` host:
      React bubbles a portal's events to the host, so every row the pointer
      crossed would raise a tooltip.
    - Such a menu opens on the press, not the click, so the menu takes the
      tooltip's place in the same frame (`useMenuTrigger`,
      [ui-design.md](ui-design.md#menus-and-submenus)).
    - A tip goes below its button unless that is near the window's bottom.
      `above` keeps a strip's tips above, on the side its menus open.
  - `InkCard` is the picture in the notes.
  - For `markup/` the file exports `InkTooltips`, `inkColorName`,
    `ERASER_SIZES`, `ColorChoices` and `swallowClick` (eats the click a
    handled pointer-up would deliver).
  - `ColorChoices` is a palette role's swatches plus a custom colour, for a
    pen preset's row, a text box's colours and the selection's Color and
    Fill rows.
    - The swatch of the colour it is given is ringed (`.selected`,
      `aria-pressed`). Where the palette holds that colour at more than one
      alpha, only the swatch at the alpha it is given is ringed
      (`shared/model/palette.js` `swatchMarker`).
    - A colour off the palette rings the custom swatch, which shows it
      through `--ink-custom`. Given no colour (a mix, or Fill's None) it
      marks nothing.
    - The custom swatch's native picker is invisible, so its keyboard focus
      rings the swatch (`.inkCustomColor:has(input:focus-visible)`), as on
      every swatch.
  - The strip, the selection menu and the text box read the account's
    palette through `usePalette` (`shared/ui/paletteContext.js`).
  - Imports run from `markup/` to `ink/`, never back.
  - Claiming input: a capture-phase `pointerdown` listener on the page
    wrapper takes the pointer when a tool is armed or a stylus touches the
    page (`pointerType === "pen"` with *Stylus draws right away*). So text
    selection and the area drag never see it, and other pointers pass
    through.
    - Pointer-up encodes the stroke and swallows the click it would deliver
      to whatever lies beneath.
    - Lost capture, `pointercancel` and window blur discard the unfinished
      stroke.
    - A press on what `NOT_INK` names never starts ink, a stylus's
      included: the menus, the lasso's handles (a picture's are the same
      `.inkTransformHandle`), the ruler's knob and ends, the zoom window's
      band and corner, a text box's width handle, band and open editor, a
      selected picture and its crop (`.pagePicture.selected`,
      `.pictureCrop`), and the comment cards beside a PDF page. So a
      picture placed while a pen is armed moves before the pen writes over
      it. With an ink tool armed, an unselected picture takes no pointer
      (`markup.css`), and ink draws over it.
    - On a read-only page the strokes take no pointer, and a tap near one
      shows its note. The page's own clickable marks come first
      (`ABOVE_PASSIVE_INK`): a highlight, a note badge, a link box, a text
      box and a picture.
  - Touch: non-passive capture `touchstart`/`touchmove` listeners cancel
    the Pencil's touch gesture on iPad Safari (cancelling the pointer events
    alone does not stop native panning).
    - They match stylus touches, or any touch while a pen pointer is down,
      and leave finger scrolling and pinch zoom alone between strokes.
    - The global `html { touch-action: manipulation }` (app.css) removes
      Chrome's double-tap zoom while keeping panning and pinch zoom.
  - Palms: while a pen is down, finger touches on that page are swallowed
    and kept from the viewer's pan/pinch handlers. A pen takes over an
    unfinished finger stroke when the palm landed first, and a second
    contact never takes over a pen.
  - A second finger makes a finger's stroke a pinch: the stroke in
    progress is discarded.
  - A finger moving alone over a page with a tool armed and *Pen only* on
    calls `onFingerScroll` (one of the page tools' actions), once a session
    (`fingerHint`, which a pen press also spends). App puts the hint on the
    status pill ([ui-design.md](ui-design.md#the-status-pill)).
  - Samples (`ink/inkInput.js`):
    - It takes `getCoalescedEvents()` where available (Safari since 18.2;
      older Safari delivers 120/240 Hz moves without it) and hardware
      timestamps.
    - The pressure preference is snapshotted at stroke start. Off, a
      stylus's stroke commits as `pen: false`, like a mouse's: there is no
      pressure to read, so it is even, and a Brush follows the speed law.
    - A pen's tilt is `penTilt`, integer degrees, none when upright.
    - The pointer-up position comes with the last contact pressure and tilt
      (up reports zero), and repeated points are dropped.
    - A pen's twist is `penTwist`, integer degrees 0..359, clockwise as
      seen, null for a mouse or a finger. Its value at the first sample
      sets a calligraphy stroke's `nib` on the draw context
      (`strokeNib(preset nib, twist, preset nibLock)`).
    - The live outline gets the same endpoint treatment as saved ink, so
      it reaches the pen tip.
    - With `getPredictedEvents()` the pen preview adds at most 16 ms / 12
      CSS px of prediction, expiring after 32 ms and never encoded.
  - Canvas: the live canvas covers what shows of the layer, not the whole
    page.
    - At the down it is laid on where the layer's rect meets the box it is
      seen through (`viewportOf`: the zoom window's view, the PDF viewer,
      the nearest scroller, else the window; `canvasWindow` in
      `shared/lib/canvasSize.js`, the draw context's `cw`). It is placed in
      the layer's own percentages.
    - It grows (`growCanvas`) only when a frame reaches past it while more
      of the layer shows than at the down (a resize since): what lies
      outside the view is never seen.
    - So over a page magnified past its viewer (the zoom window's at 4×, a
      page at 400 %) the line under the pen keeps the device's pixel ratio.
      A pen-down allocates a canvas of the view, not one of the whole page
      held to a ratio near 1 by the caps.
    - `canvasSize` still caps the bitmap (8 Mi pixels / 4096 per edge on an
      iPad or a touch-first device, 16 Mi / 8192 on a desktop). The context
      transform (`windowMatrix`) uses the real backing-to-page ratio and
      the window's offset.
    - Lift and cancel release the bitmap.
    - Canvas and SVG share the dark-page colour filter, and so does a text
      box's body.
  - Selection: the lasso draws its polygon on the same canvas.
    - A drag inside the selection box moves the selected strokes (previewed
      as a translated copy, committed on pointer-up).
    - A pending tap/hold state, separate from the drawing, lets a native
      scroll cancel a touch selection without ink.
  - Smoothing: the draw context snapshots the preset's `stabilize` at the
    down (as it does `pressure`) and holds a `makeStabilizer(level, {k})`.
    - The layer's `takeSample` pushes each pointer sample (`inkSample`)
      through it and keeps what comes back (a preset without smoothing goes
      through `appendInkSample`).
    - The lift appends `finish`'s catch-up, and prediction is off (the raw
      predicted positions would spike from the brush to the pointer).
    - The live canvas draws the stabilised samples, so the line under the
      pen is the committed line. At level 4 it also draws a faint 1 px
      string from the brush to the pointer.
    - A hold recognises the samples plus the tip, so a held line ends where
      the pen rests, not a string's length short of it.
    - The iPad `claim.commit` feeds the host's raw samples through the same
      stabilizer before `finishStroke`.
  - Draw and hold: with `ink.drawHold` on (and, for a highlighter, the
    preset's `straight` not `'off'`), a hold timer on the draw context
    restarts whenever the tip moves more than `HOLD_MOVE`.
    - It fires after `HOLD_MS` still when `holdQualifies(d.samples, k)`
      (the stroke's extent at the size floor) and the stroke lasted
      `HOLD_MIN_MS` before the tip went still (`holdFire`; the numbers are
      `shapes.js`'s, above).
    - On firing, `recognise(d.samples, {k, kinds})` (`['line']` for a
      highlighter) sets `d.hold = {kind, params, snap: params, from: the
      tip, at: now}` on a hit.
    - The canvas then draws `shapeSamples` of it instead of the trace: two
      frames in the `inkSelRect` blue, then the preset's look at its
      constant width, as the shape commits. Its style carries the shape,
      so it has no width law: no Brush tapers, no Pencil shading.
    - Once the tip has moved `REFIT_PX`, every further move refits through
      `refitShape(kind, snap, {pointer, from, shift: e.shiftKey})`.
    - A move of more than `CANCEL_PX` within `CANCEL_MS` of the snap clears
      `d.hold` and the freehand trace resumes.
    - Shift with a mouse (`pointerType === "mouse"`) recognises at once.
      The timer is cleared in `handOver`, `finish` and `cancel`.
    - `commitStroke` passes `hold: d.hold` to `finishStroke` and the
      result's `status` to `onHoldStatus`.
    - A pen stroke that snaps calls `onHaptic('snap', tip)`, the tip in
      viewport CSS px, which `usePencil`'s `hostHaptic` sends on only under
      a host.
  - The iPad host (`nativeHost().ink`, [ipad.md](ipad.md) "The pen"): a
    pen down claims the stroke (`claimForHost`) with the claim's tool,
    `claimTool(look, {k})` (`ink/hostInk.js`, below). The claim is
    `{taken, commit(samples, held), hold(samples), held}` in the module's
    `hostClaims`.
    - Under `nativeHost().hold` the module also answers `hold` through the
      claim's `hold`:
      - A claim not taken, one keeping a shape already, or one along the
        ruler answers `{}`.
      - With `pasteArmed` and every sample within `PASTE_MOVE` of the
        first, the claim ends there (out of `hostClaims`, `hostOwned`
        cleared, its pointer `pasteHold`). *Paste here* opens at the down,
        and the answer is `{cancel: true}`.
      - With `holdOn`, the samples in page points (the draw context's
        `toPt`) must pass `holdQualifies`, and `HOLD_MIN_MS` must have
        passed from the first sample to the anchor (`stillSince`).
      - Then `recognise` runs over them (`hostEvent`, `toPt`, the
        context's `kinds`), through the preset's stabilizer, the last
        sample closing the path, as the layer's own `snapHold` does with
        its tip.
      - A hit is kept as `held = {kind, params, from: the last sample, n:
        the samples' count, tSnap: the anchor's t + HOLD_MS}`, all in css
        px (`shapeToHost`), and answered `{shape: {kind, params}}`, with
        `onHaptic('snap', from)`. Anything else answers `{}`.
    - `commit` replays the samples through `hostEvent` (the tilt from
      degrees) and the stabilizer.
    - With `held`, when the host's `stroke` names that hold (its `held` is
      `held.n`: the shell drew the shape), `commit` gives the draw context
      `hold: shapeFromHost(…)` of `followHold(held, samples)` for
      `finishStroke`'s hold stage (none when that is null).
    - A `stroke` naming no hold (its lift came before the shell had the
      answer, and it drew the freehand line) commits freehand.
    - Under a host without `hold` nothing answers, so a captured stroke is
      never recognised.
  - `ink/hostInk.js` (pure): the iPad shell's side of a stroke.
    - `claimTool(look, {k}) → tool`: `{kind, color, size: size × k,
      opacity}` and the look, each left out at its default:
      - `brush` for a pen of a known brush other than the plain Pen;
      - `pressure: false` when the look reads no pressure;
      - `dash` as `dashPattern(look)` × k (css px);
      - `blend: 'multiply'` for a highlighter;
      - `stabilize` when the preset smooths;
      - for a calligraphy pen, `nib` (the preset's angle, 0..179) with
        `nibRoll: true` unless the preset locks it.
    - The ruler's `snap` is added by the layer's claim, beside the tool's
      look (`{x0, y0, angle}`: the rail's origin in viewport css px, the
      ruler's angle).
    - `hostEvent(sample) → {clientX, clientY, pressure, timeStamp,
      altitudeAngle?, azimuthAngle?, twist?}` is a host sample as the
      pointer event `inkSample` reads. The sample is `[x, y, p, t]`, `[x,
      y, p, t, a, z]` or, from a shell with `pencilRoll`, `[x, y, p, t, a,
      z, r]`.
      - The degrees are turned to radians, so `penTilt` rounds and drops
        an upright pencil as it does a browser's, and the roll `r` becomes
        `twist`.
      - `claim.commit` sets the stroke's nib from the first sample's.
    - `shapeToHost(shape, {left, top, k})` and `shapeFromHost(shape,
      {left, top, k})` turn a shape record between page points and
      viewport css px (`ink.js` `mapShape`: points moved, lengths scaled,
      angles kept, on the 1/100 grid).
  - Paste here: with `ink.clip` (the clipboard holds strokes) and a pen or
    highlighter preset armed, a pen or mouse down in stroke mode arms the
    draw-and-hold timer, with no size floor for a paste.
    - When it fires (`HOLD_MS` still) before the tip has travelled
      `PASTE_MOVE`, the draft is cancelled and nothing committed. A one-row
      `ContextMenu` (a `MenuItem` *Paste here*, `ClipboardIcon`) opens at
      the point.
    - Its row calls `onPaste(page, pt, {width, height})` with the point in
      page points and the surface's size.
    - The draft's `pasteArmed` flag and its `travel` (the farthest the tip
      went from the down, css px) are what `holdFire` reads, before the
      shape check. The lift that follows is not a click (`pasteHold`).
    - A finger never starts it: the flag checks `pointerType`, so a
      resting palm cannot paste.
    - The wrapper's `contextmenu` listener opens the same row for a mouse
      right-click when a tool is armed, no stroke is under the pointer and
      the clipboard holds strokes. It `preventDefault`s only then, and
      otherwise the page's own menus run.
    - No travel against the hold's size floor keeps the two apart: a tip
      that has travelled cannot paste, one that has not cannot snap.
  - Erasing: the draw context records what a pass needs.
    - `eraseSource: 'tool' | 'barrel'`: the armed eraser, or the pen's
      eraser end / barrel button (both erase the same way).
    - The down's mode (`'partial' | 'stroke'`) and radius in page units,
      and a `pass` id minted at the down (`strokeId`).
    - `sweep`, the path not yet handed over. It starts at the down point
      and takes every coalesced position of every move, in page units (a
      repeat dropped).
    - Once an animation frame the layer hands it to `onEraseAlong(page,
      path, r, {mode, pass})`, one call for all the page's groups. It keeps
      its last point as the next path's first, so the flushes join into
      one path.
    - The lift hands over the rest at once, its own position included. A
      cancel (`pointercancel`, lost capture, a second finger, window blur)
      hands over what was swept up to it.
    - The layer decides nothing about what goes: `useInkDocument`
      (`handleInkEraseAlong`) tests the path against every group, the
      eraser filter included.
    - `finish` in erase mode then calls `onEraseEnd()` once per gesture for
      `'tool'` only, whether or not anything was erased. A gesture ended by
      `pointercancel` does not.
    - The hover ring draws a small pen or highlighter glyph when
      `ink.eraserFilter` is not `'all'` (the `.inkCursor`'s `data-filter`
      shows the matching `.inkCursorGlyph` in the centre mark's place).
  - The hover footprint (`showCursor`): the `.inkCursor` takes the armed
    tool's `data-tool`.
    - With `ink.hoverPreview` on it takes a pen's `data-brush` (the
      teardrop), a pencil's leaning ellipse from `penTilt` (the pencil's
      width law at that slant, turned by the azimuth) and the preset's
      `data-dash` (`dashed` or `dotted`, the ring's border style).
    - Off, it is a round ring at the preset's width. The eraser's ring and
      glyph are the same either way.
  - **The Shapes tool** (`tool.tool === "shape"`, `{tool, kind, fill,
    style}` from the context): a down that would draw (a pen, the mouse, a
    finger where fingers draw) starts a drag.
    - Each move draws `shapeSamples` of `toolShape(kind, down, pointer,
      {shift: e.shiftKey, k})` on the live canvas at the style's constant
      width, with its fill.
    - The lift commits it through `finishStroke` with `hold: {kind,
      params}` and `pen: false`, the hold stage: constant width, `xyt`,
      `t` spread over the drag's time, `t0` the down's wall clock. It is
      labelled "ink straight line" or "ink shape", its status dropped.
    - It goes through the ordinary `onStroke`, the style carrying `fill:
      {color: style.color}` for a rectangle or an ellipse while `fill` is
      on. Nothing commits under `SHAPE_MIN_PX`.
    - It never arms the hold timer, *Paste here* or the scribble, and sends
      no wet stroke (`onInkLive`: peers see the shape once it is saved).
      The iPad host never claims it.
    - The page keeps `inkArmed`'s crosshair (`armedClasses`), and the hover
      footprint hides.
  - **The ruler** (`ruler`: this surface's, or null; `onRuler(next |
    null)`; `InkRuler`):
    - `InkRuler` is an anchor over the layer on the ruler's middle line,
      placed and sized in page percentages (as long as the ruler) and
      turned by its angle.
    - It is the unscaled wrapper its controls sit in, as the transform
      handles' are, so the band stays 36 css px at any zoom with no
      measuring per frame.
    - It holds the band with an SVG of its ticks and edges in css px, the
      knob and the ends, in the `inkSelRect` blue. They are outside the
      layer's SVG, so the dark-page filter never touches them and the
      band's `touch-action` holds on every engine.
    - Its parts:
      - `.inkRuler` (`data-x`, `data-y`, `data-angle`, `data-length`, page
        points and degrees, for the e2e);
      - the band `.inkRulerBand` (`touch-action: none`, its pointer and
        touch events stopped so the viewer's pan and pinch never see them;
        `tabIndex` 0, aria-label "Ruler", title "Drag to move the ruler;
        write along its edges with a pen");
      - its ticks (`rulerTicks` in `rulerUnits`) and both edges as lines;
      - the knob `.inkRulerKnob` (`role="slider"`, aria-label "Ruler
        angle", `aria-valuemin` 0, `aria-valuemax` 179, `aria-valuenow` the
        angle, title "Drag to turn the ruler; tap to snap to 15°; double
        tap to turn it 90°") with its readout;
      - an end handle each side, `.inkRulerEnd` (aria-label "Ruler end",
        title "Drag to lengthen or shorten the ruler").
    - The capture-phase `pointerdown` gates it like the selection's hit
      box. A pen on the band with a stroke tool (a pen or highlighter
      preset, or the stylus pen with nothing armed; not its eraser end or
      barrel, `writesOnBand`) starts a stroke there.
    - Every other pointer, and every pointer while the eraser, the lasso or
      the Shapes tool is armed, is left to the band (the knob and the ends
      are `NOT_INK`). The band's own handlers move the ruler: previewed in
      `InkRuler` and handed to `onRuler` at the gesture's end, each arrow
      key at once.
    - A second touch anywhere while a finger holds the band turns and
      moves the ruler with the two (`pinchRuler`). Meanwhile window-level
      capture listeners keep every touch from the page's handlers and the
      viewer's pan and pinch.
    - The touch ones are added at that finger's down and taken away at its
      gesture's end. A non-passive touch listener left on the window would
      hold every scroll in the app on the main thread (the band's own
      touches stop at the band).
    - The knob turns it (Shift, or the ruler's `snap`, in 15° steps), a tap
      snaps it (`snapAngle`) and a double press (`shared/ui/press.js`)
      turns it 90°.
    - An end handle changes its length along its direction, one end
      staying (`stretchRuler`). The arrow keys work as in "What the user
      sees".
    - Every change goes through `onRuler`. The Text tool's layer
      (`markup/TextBoxLayer.jsx`) leaves a press on the ruler to it.
    - A stroke whose down is on the band (a pen) or in the snap zone
      (`snapRail`) gets `d.rail = {origin, dir}`.
      - `takeSample` projects each sample onto it (`onRail`, `p` and `t`
        kept). Such a stroke has no smoothing and no prediction, as the
        shell's line has no lazy brush.
      - The live canvas draws the line between the farthest projections at
        the preset's constant width.
      - The hold timer, *Paste here* and the scribble stay off.
      - The lift commits through `finishStroke` with `hold: {kind: "line",
        params: railLine(samples, rail)}`: the hold stage, so it is
        constant width, `xyt`, labelled "ink straight line", its status
        dropped.
    - Under the iPad host the claim carries `snap: {x0, y0, angle}` and
      `claim.commit` projects the host's samples onto `d.rail` the same
      way.
    - A `chrome: false` layer draws no ruler and never writes along one.
  - **Shape handles** (`InkTransformHandles`): when the selection is
    exactly one stroke whose shape record `shapeHandles` knows, there is a
    button per handle at the params' points, through the live preview's
    transform.
    - Each is `.inkTransformHandle.inkShapeHandle`, `data-handle` its id,
      28 css px, with aria-labels "Line start" / "Line end", "Arrow tail" /
      "Arrow tip", "Corner {n}", "Ellipse length" / "Ellipse width" and the
      title "Drag to reshape; arrow keys move it".
    - A drag previews `reshapeStroke(stroke, dragHandle(…))` and the
      release calls `onAction("reshape", {id, params})`. The arrow keys
      move a focused one 1 pt (Shift 10 pt), each press one commit.
    - The handles are `InkShapeHandles`, which `InkTransformHandles`
      renders beside its two. Each is drawn as a small ring in the
      selection blue (`.inkShapeDot`) inside its 28 px target.
    - The keyboard's focus ring is the dot's own
      (`.inkShapeHandle:focus-visible .inkShapeDot`), and the button around
      it draws none.
    - The layer draws the reshaped stroke (`reshape`) in the original's
      place while a drag goes on.
  - **Fills**: a stroke whose `strokePath` has `fill` gets a `<path
    className="inkFill">` before its outline (`fill` the stroke's fill
    colour, `fillOpacity` its opacity or 0.25), in the same band. The live
    canvas fills the same polygon (`ctx.fill`) before the outline
    (`paintLiveStroke` for a style with a `fill` on a closed `shape`).
  - **The calligraphy footprint**: `.inkCursor[data-brush="calligraphy"]`
    is the nib, a bar the preset's width long on the screen and 0.12 of
    that thick (two pixels at least), turned by its `--nib` (`rotate(−nib
    deg)`, the hovering pen's twist applied as `strokeNib` would).
  - **`chrome: false`** (the zoom window's layer) draws the strokes, the
    stroke in progress, peers' wet strokes and the selection's dashed box,
    whose drag moves the selection as on the page.
    - It renders no `InkSelectionMenu`, no `InkTransformHandles` (nor shape
      handles), no *Paste here* menu, no ruler and no zoom overlay.
    - It registers no finger chords (the page's layer has them), raises no
      finger-scroll hint (`onFingerScroll`: nothing scrolls there) and
      reports no `onInkFeatures`.
    - After `onStroke` it hands each stroke it committed to
      `onZoomStroke(strokeBounds(stroke))`.
    - Its viewport (`viewportOf`, for the culling and for the live and wet
      canvases, which cover the view, not the magnified page) is the
      panel's view.
    - The page's selection menu leaves a press in the panel alone
      (`ignoreRef` covers `.inkZoomPanel`). So the panel's drag of the
      selection, its bar and its strokes never dismiss it by a press, and a
      stroke there deselects as one on the page does.
  - **The zoom overlay** (`zoom`: this page's window, or null;
    `onZoom(page, patch)`): `.inkZoomRect` over the window's `{x, y, w,
    h}` in the selection blue (`data-x`, `data-y`, `data-w`, `data-h` in
    page points, for the e2e).
    - It has its top band `.inkZoomBand` (aria-label "Zoom window: drag to
      move") and its corner `.inkZoomResize` (aria-label "Zoom window: drag
      to resize").
    - Both are `NOT_INK` and `touch-action: none`, buttons that take their
      press in the capture phase, before the page's layers (a text box's
      Text tool included) and the viewer see it.
    - A drag draws the box where the window would go (`preview`, by
      `zoomWindow.js` `moveWindow` and `setMag`, `useInkZoom`'s rules), at
      most once a frame.
    - At the release a drag of the band calls `onZoom(page, {x, y})`, and
      one of the corner `onZoom(page, {mag})` (`magForWidth`: `w × mag`
      over the new width, held to 2..4).
    - The window is the page tools' for every page, so moving it each frame
      would draw every page's layers again each frame.
    - A focused band's arrow keys move the window 1 pt (Shift 10), and a
      focused corner's step the magnification by 0.25, each press at once.
    - On any PDF page while a window is open, a blank tap calls
      `onZoom(page, {at: {x, y}, size})`, `size` that page's in points. The
      tap is a finger's tap, or a mouse click with the hand or the lasso,
      that selects no stroke and dismisses no selection, on no highlight,
      link, text box or picture (`ABOVE_PASSIVE_INK`).
    - Only the page's layers do, never the panel's: MarkupLayers passes
      `onZoom` only to a `chrome` layer, and only while a window is open.
  - Finger chords: `wireChords` installs one module-level
    `makeChordDetector` (the `hostClaims` pattern: a singleton, so several
    page layers never double-fire). It sits behind its own four document
    capture listeners (`pointerdown`/`move`/`up`/`cancel`) and a window
    `blur` that resets it.
    - Each layer with an `onUndoGesture` registers its wrap element in
      `chordHosts`.
    - The first touch of a chord that lands on a layer makes it the host
      whose `fingerUndo` sets the mode (`setMode`) and whose
      `onUndoGesture(kind)` the chord fires. A touch off every layer
      changes neither.
    - A touch while the host's `penBusy()` says a pen is writing (the
      page's own pen stroke, or one the native host took, whose pointer
      WebKit has cancelled) is a palm (`palm()`).
    - The wiring goes with the last host's cleanup, so chords exist exactly
      where handwriting can be drawn.
    - The detector forgets a contact whose lift never came after 1 s, so a
      dropped pointerup never pins every later chord.
    - In finger-draw mode the chord's first finger starts a stroke and the
      second cancels it as a pinch. That stroke counts toward the chord.
    - The stroke start's `onSelect(page, [])` is deferred until the stroke
      has moved 8 css px, as the pending path defers a tap, so a chord
      never clears the selection it is about to restore.
  - The live canvas draws the stroke in progress from the brush table.
    - The samples' live `w` comes by `widthLaw`, a brush's start taper only,
      at its full length. The end taper, a short mark's shorter tapers and
      the speed law's final values come with `bakeWidths` at the lift.
    - It uses `setLineDash(dashPattern)` for a constant-width dashed preset
      and the dashed runs for a pressure one, each outlined as a piece
      (`runOutline`).
    - The pencil is round **dabs** every `width / 4` of arc, each `size ×
      w` across, the band the saved outline covers, so the line under the
      pen is the committed one.
    - The lean is in `w` already, as in the saved stroke. An ellipse along
      the azimuth would draw a stroke written along it up to 60 % thinner
      than it saves.
    - The dabs are filled with `createPattern(grainImage(color),
      "repeat")` (scaled to `GRAIN_UNITS`, so it meets the SVG's grain),
      source-over. The dabs of one opacity band are one path filled once
      at `globalAlpha = opacity × band` (the shades the SVG draws), so dabs
      never darken where they overlap.
    - That is `paintLiveStroke`, which a peer's wet stroke goes through
      too.
    - A calligraphy stroke is `nibOutline` of its samples so far, one
      `Path2D` filled once (nonzero), the shape it commits.
    - A stroke along the ruler, a Shapes drag and a held shape draw their
      line or shape at the preset's constant width, with a fill when the
      shape has one.
    - **Dirty rects**: each frame of a stroke clears only the union of the
      last frame's box and this one's before the stroke is painted again
      (`paintStroke`, `canvasCtx(d, dirty)`). The box covers the samples,
      and the prediction's and the smoothing string's tip, padded by two
      sizes, the widest any brush draws.
    - So a long stroke never clears the whole page's bitmap. The lasso
      clears whole, and peers' wet strokes have a canvas of their own.
  - `ink/grain.js` writes the grain tile, a 64 × 64 (`GRAIN_PX`) PNG of
    speckles at three strengths over 30 % bare paper, as a 2-bit palette
    image.
    - The speckles are white noise over a coarser, smooth paper tooth, from
      a fixed seed, so every client draws the same grain. The chunks' CRC
      comes from `shared/lib/crc32.js`.
    - `gamma/ink.py` `grain_png` writes the same PNG byte for byte for the
      exporters (`tests/shared/inkgrain.json`, "Server").
    - `grainUri(color)` is the tile in that colour, built once per colour.
      Only its palette differs, so no SVG filter tints it.
    - `grainImage(color)` is the tile decoded for `createPattern` (null
      until decoded, and in node).
    - `grainHex(color)` names the tile a colour is painted with, its r, g
      and b as six hex digits.
    - The layer's `<defs>` hold one `<pattern id="inkGrain-<layer>-<rrggbb>"
      patternUnits="userSpaceOnUse">` per tile the surface's pencils need,
      holding an `<image href={grainUri("#rrggbb")}>`. It is `GRAIN_UNITS`
      (32) page units square: two grains a point, so the grain zooms with
      the paper and a 2 pt line is four grains wide.
    - A pencil band fills with `url(#inkGrain-…)` (`GrainContext` hands
      `Strokes` the fill, and a notes card's SVG has its own).
    - Colours of one r, g and b share a pattern and no two tiles share a
      name, as on the server (`inkGrain-<rrggbb>`).
    - There is one pattern per tile per SVG, never per stroke. A mouse
      pencil, which has no shades, fills with it too.
  - **Wet strokes** (`ink-live`, [collab.md](collab.md) "presence";
    `ink/inkLive.js`, pure, both halves). A stroke's id is minted at the
    down (`strokeId`), and the committed stroke is encoded under it.
    - The layer sends the stroke through `onInkLive(page, msg)`:
      - at most every 50 ms while drawing (`LIVE_MS`, a trailing timer),
        the samples since the last chunk, with `n` the index of the chunk's
        first sample and the look the stroke commits with;
      - on the lift, once the stroke is committed, the rest, the last chunk
        (possibly empty) marked `done`;
      - `drop: true` when it ends uncommitted (a cancel, a scribble that
        erased, the paste hold, a lost pointer).
    - The samples are `liveSamples`: `[x, y, p, t, a, z]`, page points to
      two decimals, `p` 0..1 or null without pressure, `t` ms since the
      stroke's first sample, integer degrees or null. A chunk holds at most
      `LIVE_SAMPLES` 64.
    - The look is `liveStyle`, a calligraphy pen's `nib` with it, which the
      receiving side keeps when it is an integer 0..179.
    - A committed stroke an edit takes off every group before a saved file
      holds it gets a `drop` too, from `ink/useInkLive.js`. Such an edit is
      an undo, an erasure, a cut, a delete within the 700 ms before the
      first flush.
    - Its `liveLedger` (`inkLive.js`) keeps the ids sent live per surface
      until a save holds them (`saved`, after `markSaved`). `applyInk` asks
      it which of an edit's strokes left every group (`removed`; a stroke
      moved to another group stays), since no file would ever land them on
      the peers' pages.
    - A host-claimed iPad stroke sends nothing: peers see it when its file
      lands.
    - The peers' chunks land in `inkLive`'s store, by surface, keyed
      `client/id`. In `receiveInkLive` a first chunk starts a wet stroke, a
      chunk whose `n` is the count held adds to it, and any other drops it,
      so a missed chunk draws nothing more until the committed stroke
      lands.
    - It is not React state: each layer subscribes to its own surface
      (`useWetStrokes`) and paints the wet strokes on a canvas of their own
      (`.inkWetCanvas`).
    - That canvas is under the live canvas, cleared whole per paint and
      laid on what shows of the surface as the live canvas is. While it has
      strokes it is painted again when a scroll or a resize brings more of
      the surface into view.
    - The paint goes through `paintLiveStroke` with the style's width law
      baked on the fly (start taper only until `done`). So a peer's 20
      chunks a second repaint one canvas and re-render nothing.
    - A wet stroke goes when any group drawn on its surface holds its id
      (`inkLive.landWet`, run on every paint and render). Its file has
      landed then, and the committed stroke stands where it was.
    - It also goes on `drop`, when its writer leaves the page
      (`useInkLive`'s `keepWetOf(clients)`), with the page (`clearWet`),
      and 60 s after its last chunk (`WET_MS`).
    - The canvas's `data-wet` counts the wet strokes and `data-wet-ids`
      names them, for the e2e.
    - A read-only surface without ink mounts its layer while it has wet
      strokes (`useHasWet`, `MarkupLayers.jsx`).
  - **Dense pages** (`useVisibleGroups`, `useIdleOutlines`,
    `useGroupRasters`): none of it changes a pixel of what is drawn once
    the outlines are built. A tap's selection, the lasso and the eraser
    keep reading the samples, and only the picture lets presses through
    (below).
    - **Culling**: on a surface holding more than `IDLE_STROKES` (200)
      strokes, a group whose `inkBounds` box lies outside the part of the
      page in its scroller (`viewportOf`), plus half that scroller on every
      side, renders its `<g>` empty.
      - The layer measures it itself, before the paint of every render and
        on every scroll frame. An `IntersectionObserver` on the page (root
        the scroller, margin 50 %) tells when the whole page is that far
        off.
      - A layer mounted with its surface (a switch between the notes and
        notebook views, the ink already loaded) finds the surface's ref
        unset in its layout effects, since React attaches a parent's ref
        after its children's. It sets the measuring up one render later.
    - **Idle outlines**: a group of more than `IDLE_STROKES` strokes with
      more than a slice (50) of them not built yet draws those as
      `strokePlaceholder` polylines.
      - It builds the outlines in `requestIdleCallback` slices of 50
        (`setTimeout` 0 without it), each slice swapped in as it lands, for
        the groups on screen.
      - A stroke or two not built yet (a new stroke, a reload's additions)
        is built at once, so a lifted stroke never shows thin.
    - **The raster**: on a surface holding more than `RASTER_STROKES`
      (1 500) strokes, every finished group's `<g>` is drawn, once it has
      stood unchanged for a second, as a picture of itself.
      - That is each `<g>` (one per band) but the group the layer's last
        stroke went into, one with a selection or in a replay, and one
        culled.
      - The picture is its own markup, with the layer's `<defs>`, as an SVG
        image drawn to a canvas at the current zoom in device pixels and
        kept as a PNG. It is shown as one `<image class="inkRaster">` in its
        `<g>` (multiplied onto the page for band 0) until the group
        changes.
      - The picture takes no presses (`pointer-events: none`). An image is
        hit over its whole box, where the paths are hit on their ink alone,
        so a group's box would hide the page's text and links from a press
        in Hand mode.
      - A tap on the ink still selects it, from the samples
        (`nearestInkStroke`), and a press on the ink itself reaches the
        page as one beside it would.
      - A zoom draws the picture again from the markup it kept, the old one
        standing meanwhile. A group too large for a canvas (`canvasSize`'s
        caps) keeps its paths, its picture at the old zoom dropped.
      - A layer's pictures hold at most `RASTER_PIXELS` (16 Mi device
        pixels, 64 MB decoded) together. One that would pass it drops the
        pictures of groups scrolled away first, else keeps its paths at
        that zoom.
      - A browser that taints the canvas with the SVG image shows the SVG
        image itself.
- `ink/surfaceScale.js` (pure but for a registry;
  `frontend/tests/surfaceScale.test.mjs`): a screen distance in a
  surface's page units.
  - The `InkLayer` with chrome registers its surface by `pageNumber` (a PDF
    page's number, a sheet's id) through `measureSurface(surface,
    measure)`. `measure() → {css, width}`, read at each ask, is its
    wrapper's css width and the surface's width in units.
  - The zoom window's copy shows the surface at another scale and never
    registers.
  - The call `measureSurface` returns unregisters, unless another layer has
    registered the surface since.
  - `surfaceUnits(surface, px)` converts with `pxToUnits(px, css, width)`,
    a scale of 1 when nothing is measured.
  - `duplicateOffset(surface)` is `DUPLICATE_PX` (12) css px as `{dx, dy}`,
    which the menu's Duplicate and Ctrl+D (`ink/useInkSelection.js`
    `duplicateInkSelection`) both send.
- `ink/ZoomWindow.jsx`: the zoom window's panel, which `PdfViewer` renders
  docked under its scroller while the page tools' `ink.zoom` is on one of
  its document's pages.
  - The scroller gives up the panel's height: PdfViewer's `zoomHeight`,
    `onZoomHeight(px)` and `onZoomChange(update)` props, App's
    `inkZoomHeight` and `useInkZoom`'s `changeInkZoom`.
  - Props:
    - `zoom`, `size` (the page's `{width, height}` in points) and `page`
      (the pdf.js page, null until it loads), with its `marks`;
    - `height` and `onHeight(px)`;
    - `onChange(update)`, an update of the window, `update(window) →
      next`, so it always applies to the window as it is then, on that
      page only;
    - `onClose()`;
    - the viewer's `annotationMode`, `isCancel` and `dark` (its flipped
      page colours).
  - `.inkZoomPanel` (`role="region"`, aria-label "Zoom window",
    `data-guide="ink.zoom"`, `data-page`, `data-mag`, the surface's
    `armedClasses`; `useInkZoom` closes it under the phone layout) holds
    the grip, the bar and the view.
  - The grip `.inkZoomGrip`: `role="separator"`,
    `aria-orientation="horizontal"`, aria-label "Zoom window height",
    `aria-valuenow` in css px, 160 to 320, and a drag or ↑ / ↓ by 10 px.
    - A drag keeps its height in the panel's state (`dragHeight`, which
      the panel's height and `aria-valuenow` follow). It sets it beside the
      panel as it moves (`previewHeight`: the sibling `.pdfViewer`'s
      height, in the `calc(100% - Hpx)` form PdfViewer renders, and App's
      `--ink-zoom-h` on `.viewerWrap`).
    - The window's size waits too (`sized()` runs once the drag ends).
    - `onHeight` runs once, on `pointerup`, `pointercancel` or
      `lostpointercapture`, when the height changed. So the preference and
      App's render, which reaches every page's layers, change once a drag.
    - ↑ / ↓ call `onHeight` at each press, except during a drag, which owns
      the height until it ends.
  - The bar `.inkZoomBar` (`role="toolbar"`, aria-label "Zoom window
    controls"):
    - *Previous line* `ChevronLeftIcon`, *Next line* `ChevronRightIcon`,
      *New line* `CornerDownLeftIcon` titled "New line · Enter";
    - the Line height group (`role="group"`, aria-label "Line height":
      `MinusIcon` "Smaller line height", the readout "24 pt", `PlusIcon`
      "Larger line height");
    - the Magnification group (`role="group"`, aria-label "Magnification":
      the pills *2×*, *3×*, *4×*, `aria-pressed`, titled "Magnify 2×" …);
    - *Close* `XIcon`, aria-label "Close the zoom window", title "Close the
      zoom window (Z)".
  - The view `.inkZoomView` is `touch-action: none` at rest whether or not
    fingers draw, with two-finger touches stopped before the viewer's
    pinch. Inside it the page `.inkZoomPage`, `width × mag` by `height ×
    mag` css px, is moved by `(−x × mag, −y × mag)` from the window's
    rect, the one source of truth (nothing reads a scroll position back).
  - The page's pixels behind it come from `pdf/pageRaster.js`
    `renderRegion` (`zoomRasterPlan`).
    - The plan is the window and a margin of 0.65 of its width on either
      side and half its height above and below. So an auto-advance along
      the line or a step to the next line lands on pixels already drawn.
    - They are drawn at `mag` × the device pixel ratio, never the whole
      page (a 4× page would pass the canvas caps).
    - They go into two canvases, the hidden one drawn and then shown, and
      are drawn again 80 ms after the window leaves what `zoomRasterNeed`
      says is covered.
    - During a grip drag, which sizes the window only at the release, both
      plan for the window as large as the view. So a taller panel fills
      with the page whenever the drag pauses.
  - The panel's paper and raster take the viewer's treatment for the
    theme and for Flip page colors (`.inkZoomPanel.pdfDark`), in the
    viewer's rule order. So Gamma Dark keeps its paper with the flip on
    too.
  - Over them is a `<MarkupLayers chrome={false}>` for the page (same
    surface, same groups, same marks, no text boxes). What it commits
    joins the page's current group through the same `onStroke`, so
    storage, replay and sync see nothing new.
    - Its `onZoomStroke(box)` runs `advance`.
    - The panel measures its view and keeps `w` and `h` (`sized`), and the
      main viewer scrolls the window's box into view (`revealDelta`) when
      it moves.
  - The viewer's own bottom controls (the close and full-screen buttons,
    the recording pill, the player bar) stand above the panel while it is
    open (`.viewerWrap.inkZoomOpen`, `--ink-zoom-h`).
- `ink/buildFence.js`: the build fence, `useBuildFence({sessionBuild,
  setStatus}) → {inkStale, noteServerBuild}`, which App calls above the
  strip's owner with its session's build.
  - `noteServerBuild` keeps the names of the server's build as the session
    reported it (`/api/session` `build`: commit, label, version). It
    compares every socket hello's `build` with them (`usePageCollab`'s
    `onBuild`).
  - A build sharing none of the names sets `inkStale`, which disarms the
    tools (`inkTool`, `inkPenTool` null) behind the sticky pill "Gamma was
    updated, reload to keep drawing" with a Reload button. Taps, selection
    and reading go on.
  - The comparison is pure (`frontend/tests/buildFence.test.mjs`).
    `buildNames(build)` is a build's names, and `sameBuild(seen, names)`
    whether they share one with the build seen first.
  - `buildTrips(fence, build)` keeps the first build with names and answers
    whether this one trips the fence, which happens once.
- `ink/useInkStrip.js`: the tool strip's state, `useInkStrip({focusedBlockId,
  readOnly, inkStale, inkTools, setInkToolsRaw, inkAutoPen, inkShapeKind,
  inkShapeFill, inkEraserReturn})`, which App calls above the text boxes
  (an armed ink tool makes them inert).
  - It returns the state and what derives from it (`inkUi`, `inkVisible`,
    `inkPenPreset`, `inkPen`, `inkTool`, `textArmed`, `inkPenTool`), its
    writes (`setInkTools`, `pickInkTool`, `openInkStrip`, `toggleInkShape`,
    `inkEraserLifted`) and `previousTool()`.
  - It also returns one command for each update the rest of the page makes
    to the strip, the same function for the page's life:
    - `revealInkStrip` (a lasso selection);
    - `openRulerRow` and `closeRulerRow` (the ruler laid and put away);
    - `closeInkStripUi` (App's `closeInkStrip`);
    - `toggleInkOptions(which)` and `closeInkOptions` (the strip's buttons
      and keys);
    - `applyPencilUi(ui)` (a Pencil gesture's `{ui}`);
    - `disarmInk` (a recording's Play) and `restorePen` (the guide's
      `restore("pen")`).
  - Leaving a page disarms the tool, and the strip stays open.
  - `inkUi` holds:
    - `open`;
    - the armed `tool`: a preset id, `eraser`, `select`, `text` for the
      Text tool, `shape` for the Shapes tool, or `null` for the hand;
    - its `options` row (`true`, or `"ruler"` for the ruler's);
    - `pen`, the last pen preset, which a stylus writes with when nothing
      is armed. It is resolved among the visible presets, so a hidden pen
      falls back to the first visible pen, else the first visible preset
      (`pencilActions.js` `stylusPen`, `inkPenPreset`).
  - `"shape"` is one of the reserved tool ids (`pencilActions.js`
    `RESERVED_TOOLS`), which the strip's effect that drops a removed or
    hidden preset, its `restorePen` (the guide's `restore`) and `backTool`
    read. `inkTool` for it is `{tool: "shape", kind: inkShapeKind, fill:
    inkShapeFill, style}`.
  - `toggleInkShape()` arms `"shape"` with its row open, or gives back
    `inkBack()`.
  - `inkTools`, the account's preset list, validated by `ink/ink.js`
    `normalizeTools`. `setInkTools` runs every write through it, the
    updater form the pill's *Show* passes included, so the stored and
    pushed list is the canonical one.
    - `normalizeTools` keeps a preset's `stabilize` 0–4 as an integer,
      omitted at 0 except on a calligraphy pen, whose 0 is its Off. Left
      out, a calligraphy pen smooths at `CALLIGRAPHY_SMOOTHING`, 2
      (`presetSmoothing`).
    - It keeps a pen's `brush` among `monoline`, `brush`, `pencil`,
      `calligraphy` (`KNOWN_BRUSHES`). `'pen'`, an unknown value or any
      brush on a highlighter is dropped, so the plain pen is the absent
      key.
    - It keeps a `dash` of `dashed` | `dotted` on a pen or a highlighter
      (omitted when solid), and a highlighter's `straight` `'always' |
      'off'` (omitted at `'hold'` and dropped from a pen).
    - It keeps a `hidden: true` flag (omitted when false).
    - Any other key a preset carries is kept verbatim, so an older build
      that edits one preset never strips what a newer one wrote. The known
      keys come first, and the rest only while the preset serialises under
      512 bytes of UTF-8.
    - A list that hides every preset shows its first, so the row is never
      empty.
  - `toolStyle` copies `brush` and `dash` onto the style a stroke is
    encoded with. It carries the smoothing it draws with
    (`presetSmoothing`) and straightening onto the armed tool for the
    layer, never onto a stroke.
  - `inkToolName(preset)` (`markup/MarkupToolbar.jsx`) reads "{color} pen",
    "{color} ballpoint pen", "{color} brush pen", "{color} pencil",
    "{color} calligraphy pen" and "{color} highlighter" (the line is not
    named).
  - `visibleTools(list)` is the list without the hidden ones, what the
    strip, the key table and the stylus's `backTool` read.
  - `inkBack()` is the previous distinct tool, else the stylus pen
    (`pencilActions.js` `backTool` over `inkPrevRef`, which follows the
    armed tool after each commit).
  - `inkEraserLifted()`, the end of App's `handleInkEraseEnd`
    (`onEraseEnd`), picks it with the options row closed when
    `inkEraserReturn` is on and the eraser armed.
    - App's part first marks a held E (`noteInkEraseHold()`,
      `useStripKeys`) and stops while a held squeeze is open
      (`noteSqueezeUse()`, `usePencil`: its release goes back).
    - A squeeze that toggles the eraser still toggles it. With the return
      on, the lift re-arms the pen and the next squeeze arms the eraser
      again.
- `ink/stripState.js` (pure; `frontend/tests/stripState.test.mjs`): the
  strip's updates as functions of `inkUi`, one for each write of
  `useInkStrip`.
  - They are `picked` (`pickInkTool`), `revealed`, `rulerRowOpened`,
    `rulerRowClosed`, `stripClosed`, `optionsToggled`, `optionsClosed`,
    `pencilUiApplied`, `disarmedForReplay` (`disarmInk`),
    `disarmedForPage` (leaving a page), `penRestored` (`restorePen`) and
    `hiddenDropped` (a removed or hidden preset armed).
  - `armedTool({tool, readOnly, inkStale, inkTools, inkShapeKind,
    inkShapeFill, inkShapeStyle})` is `inkTool`, and `shapeStyleOf(inkPen)`
    the Shapes tool's style.
- `ink/useInkLive.js`: live co-drawing, this tab's side,
  `useInkLive({focusedBlockId, readOnly, inkStale, peers, sendInkLive}) →
  {handleInkLive, liveRemoved, liveSaved}`. App calls it after the page's
  collab session with the session's `peers` and a `sendInkLive` that reads
  the session when it runs.
  - **Live co-drawing**: `onInkLive(page, msg)` (`handleInkLive`) adds the
    surface as `page` and hands the chunk to the page's collab session.
    - The session's `sendInkLive(msg)` writes `{t: "ink-live", ...msg}` on
      the socket when it is open and drops it otherwise (never queued,
      never retried: the committed stroke follows).
    - App hands incoming `ink-live` messages (the session's `onInkLive`) to
      `inkLive.receiveInkLive`, the wet strokes' store ("Wet strokes"
      above), not App state.
    - An effect on the session's `peers` keeps only the wet strokes of
      peers still on the page (`keepWetOf`: a `leave`, or the socket
      closing, which empties the peers). Leaving the page clears them
      (`clearWet`).
    - Only a peer who `can_edit` sends (a read-only page has no edit
      actions, and the server drops a viewer's). A viewer only receives.
  - The ledger of the strokes sent live (`inkLive.js` `liveLedger`) is its
    own:
    - `liveRemoved(changes)`, which `applyInk` runs, sends the `drop` of
      each stroke an edit took off every group before a save held it;
    - `liveSaved(ink)`, which the flush runs after `markSaved`, forgets the
      strokes a saved file holds;
    - leaving the page forgets the rest.
- `ink/useInkSave.js` over `ink/inkSaver.js`: the save.
  - The saver, `createInkSaver({blocksRef, setStatus, liveSaved, inkStore,
    apiJson, API, keepaliveRoom}) → {flushInk, scheduleInk, pending}`, is
    handed the draft store, the API and the keepalive room rather than
    importing them. So `frontend/tests/inkSaver.test.mjs` drives it with
    fakes and mock timers. A real close is the scratch scenario
    `tests/e2e/tabClose.mjs`, apart from the browser suite.
  - `useInkSave({focusedBlockId, blocksRef, setStatus, liveSaved}) →
    {flushInk, scheduleInk}` keeps one for the page's life over the real
    ones. App calls it after the page's collab session, so its listeners
    register after collab's.
  - It flushes on `visibilitychange` when a save is pending (with
    keepalive when hidden), on `pagehide` when one is pending or any draft
    is dirty (keepalive, `leaving`), and when the open page changes. A
    close whose `pagehide` listener Firefox stopped flushes on going hidden
    alone ("Fences").
  - `scheduleInk()` puts the flush 700 ms after the last edit and at most
    5 s after the first unsaved one ("Fences"). `pending()` is whether one
    waits on its timer.
  - The flush starts every dirty group's requests at once
    (`ink/inkFlush.js` `planFlush` says what each sends and which go with
    `keepalive`).
    - It uploads each draft (`POST /api/upload-ink`, the `serializeInk`
      bytes) and PATCHes its block through `PUT /api/blocks/{id}`. The
      `PUT` carries `inkProps` (the url, count and position),
      `base_properties: {ink_url}`, the file the draft was edited from,
      and `base_ids`, that file's stroke ids.
    - That is a server-side writer, so the change fans out over the page
      socket and reaches this tree like a remote op. Only the group's block
      itself (first stroke) is inserted through the tree.
    - The answer's `ink_conflict` (its block's merge report) goes on the
      pill (`reportInkConflict`, "Two writers, one group"), in the words of
      `inkSaver.js` `conflictMessage(conflict)`.
  - A draft remembers its base (`inkStore` drafts: `{ink, dirty, url,
    base, baseIds}`). When the answer names another file than the upload,
    the server merged someone else's strokes.
    - `markSaved` then loads the merged file and replaces the draft with
      it, with strokes drawn meanwhile merged on top.
  - An empty group with no caption and no notes is deleted the same way.
    - Its empty draft keeps masking the saved strokes until the tree sees
      the deletion (the HTTP response can land before the socket op).
    - `inkStore.markDeleted` marks it clean only after a successful
      delete, so a failure retries.
    - An empty group with a caption or notes is saved as an empty drawing
      instead.
  - A failed flush retries after two seconds on a 5xx, a network error or
    a 404 (the block's insert may still be queued behind the tree's save).
    - Any other 4xx stops the retry and keeps the draft behind a sticky
      pill (`setStatus(msg, null, {sticky: true})`, "Two writers, one
      group", "Fences").
- `ink/useInkSurfaces.js`: the page's ink by surface,
  `useInkSurfaces({blocks, nbSheets, nbSheetsRef, nbCurrent, notebook,
  pdfUrl, pdfHidden}) → {inkBlocks, inkBySurface, onPageInView,
  pasteTarget, inkPageNumber}`, which App calls after the save.
  - `inkBlocks` is the page's ink groups as `{id, properties}`, the same
    array while their JSON is (the page marks and the replay's player are
    built from it).
  - `inkBySurface` maps a PDF page's number to its groups (by their
    `pdf_position`'s page) and a sheet's id to its own
    (`notebook/notebook.js` `inkBySheet`).
  - `pasteTarget()` is where a paste without a point goes, with its size
    in points: on a notebook the current sheet (`nbCurrent`, else the
    last) with its paper, else the PDF page in view while a PDF shows.
    - That page comes from `PdfViewer`'s `onPageInView(pageNumber,
      {width, height})`. One `IntersectionObserver` over the page boxes
      keeps the intersecting ones, and per scroll frame the one whose
      centre lies nearest the scroller's centre is reported when it
      changes, with its size at scale 1.
    - Another document (`pdfUrl`) forgets it until its own viewer reports
      a page, so a paste while it loads never lands on a page of the last
      one.
  - `inkPageNumber(page)` is what messages call a surface: a PDF page's
    number, or a sheet's place among the sheets ("?" for one gone).
  - `visiblePart(page, size)`, exported beside the hook, is the part of a
    surface's box inside its viewer and the window, in page points, or
    null when none of it shows. It is where the ruler and the zoom window
    first go.
- `ink/inkSurfaces.js` (pure; `frontend/tests/inkSurfaces.test.mjs`): what
  `useInkSurfaces` works out from its inputs.
  - `groupsBySurface(inkBlocks, nbInk)` is `inkBySurface`, and
    `surfaceLabel(page, sheets)` is `inkPageNumber`.
  - `pasteTargetOf({notebook, sheets, current, inView, pdfUrl,
    pdfHidden})` is `pasteTarget()`.
- `ink/useInkDocument.js`: the page's ink as a document,
  `useInkDocument({focusedBlockId, readOnly, blocksRef, setBlocks,
  inkBySurface, inkPageNumber, inkEraserFilter, scheduleInk, liveRemoved,
  clearInkSelection, boxUndo, undoBlocks, sayUndoable, setStatus,
  setInkFingerUndo}) → {inkHistoryState, inkOf, applyInk, inkUndo,
  undoableInkStatus, stripUndo, handleUndoGesture, handleInkEraseMany,
  handleInkEraseAlong}`, which App calls after the surfaces.
  - `inkOf(id)` is a group's current strokes: its draft, else its loaded
    file.
  - The **stroke history** (`inkHistRef`): entries of `{changes: [{id,
    page, before, after}], label}`, one per action.
    - `inkHistoryState` mirrors its lengths for the strip's buttons, and
      leaving the page empties it.
    - A group whose block is gone is re-inserted when an entry brings
      strokes back, its old draft dropped first (`inkStore.dropDraft`).
    - The block coming back is a fresh one with no file, so its upload
      must name no base. A base left from the deleted draft would make the
      server merge the strokes three ways against the new block's empty
      drawing and count them as deleted.
  - Every edit funnels through `applyInk`.
    - It checks the byte budget: an edit that would grow a group past
      `MAX_BYTES` is refused with "This handwriting note is full. Move part
      of it to a new note, or start a new one."
    - Then it updates the drafts, records the entry and schedules
      `flushInk` ("Fences": 700 ms after the last edit, at most 5 s after
      the first unsaved one, and on `visibilitychange`, `pagehide` and
      leaving the page).
  - The eraser filter is one function: `ink.js` `eraserKeep(filter) →
    (stroke) => bool`.
    - `'all'` keeps every stroke, `'pen'` tool `pen` of any brush,
      `'highlighter'` tool `highlighter`. A stroke of an unknown tool is
      kept only under `'all'`.
    - It is applied in `handleInkEraseAlong` and `handleInkEraseMany`.
      `hitStrokesAlong(ink, path, radius, keep)` and `eraseAlong(ink, path,
      radius, keep)` take it as an optional last argument and leave a
      stroke `keep` refuses untouched, the same object, so the caches
      survive.
    - The layer builds it from `ink.eraserFilter` for the scribble stage
      and the hover glyph.
  - `handleInkEraseAlong(page, path, r, {mode, pass})` is the eraser's
    flush: `eraseAlong` (partial) or `hitStrokesAlong` and `removeStrokes`
    (whole strokes) over every group of the surface (`inkBySurface`)
    through the filter. The changes of all of them are one `applyInk`.
    - Every flush of one pass folds into one history entry, `T("partial
      ink erasure")` or `T("ink erasure")`, keyed by the pass. `applyInk`'s
      `pass` option keeps it on the entry, and `applyInk` answers whether
      the edit applied, since the byte budget may refuse it.
    - A group the entry holds takes the flush's `after`, and another joins
      it with its `{id, page, before, after}`. So one Ctrl+Z undoes the
      pass across groups.
    - A group whose drawing is no longer the entry's `after` (a save that
      merged someone's strokes in meanwhile) starts a new entry instead, so
      undoing the pass never takes their strokes away.
  - `handleInkEraseMany(page, targets)` deletes the targets across their
    groups as one entry, `T("ink scribble erase")`. It raises
    `undoableInkStatus(tn("Erased {n} stroke.", "Erased {n} strokes.",
    n))`.
  - `undoableInkStatus(msg)` is App's `sayUndoable(msg, run)` with the
    stroke history's step for `run` (`inkUndo(false)`), as the strip's
    button does. App's `undoableStatus(msg)` runs the `app.undo` command
    instead, the block history.
  - `stripUndo(redo)` is the strip's Undo / Redo buttons and a Pencil
    gesture's Undo: `undoBlocks(redo)` under `boxUndo`, else
    `inkUndo(redo)`.
  - `onUndoGesture(kind)` (`handleUndoGesture`) runs what the strip's Undo
    / Redo buttons run: `inkUndo(redo)`, or `undoBlocks(redo)` under
    `boxUndo`.
    - The first chord of a session also raises a pill, "Two fingers undid
      a stroke." / "Three fingers redid a stroke.", with a *Turn off*
      action that sets `inkFingerUndo` to `'off'` (`fingerUndoHintRef`,
      spent by the first chord).
  - **The journal pill**: `inkStore.onInkRestored` raises "Unsaved
    handwriting from a previous visit was restored." with `{label:
    t("Discard"), run}`, once per block (a block is restored at most once
    a visit). It schedules the flush that saves the restored draft.
- `ink/inkHistory.js` (pure; `frontend/tests/inkHistory.test.mjs`): the
  stroke history's rules, which `useInkDocument` runs on `inkHistRef`.
  - `recordEntry(h, changes, {label, pass})` adds an edit's entry, keeps
    the last `HISTORY_CAP` (200) and empties redo.
  - `stepChanges(entry, redo, inkOf)` is an undo or a redo onto the
    drawings as they stand, with `kept`, the strokes someone changed
    since.
  - `passFold(h, changes, pass)` and `foldInto(last, changes, held)` fold
    an eraser pass's flushes into one entry.
  - `undoMessage(entry, redo, kept, page)` is the pill.
- `ink/inkEdits.js` (pure; `frontend/tests/inkEdits.test.mjs`): the
  arithmetic of the edits `useInkDocument` and `useInkStrokes` make.
  - `overBudget(changes)` is the byte budget.
  - `missingGroups(changes, present)` and `withGroupBlocks(prev, missing)`
    put back a group's block (`prev` the block tree).
  - `freshInk(page, size)` is a new group's file.
  - `splitsGroup(before, bytes, count)` says when strokes start the
    surface's next group, and `pasteTooBig(page, strokes, size)` when no
    group can hold a paste.
  - `eraseManyChanges` and `eraseAlongChanges` are the erasers' changes
    through the eraser filter.
- `ink/useInkStrokes.js`: the strokes, `useInkStrokes({focusedBlockId,
  readOnly, inkStale, blocksRef, nbSheetsRef, applyInk, addSheetAfter,
  noteSqueezeUse, undoableInkStatus, setStatus}) → {handleInkStroke,
  handleInkStrokes, handleHoldStatus}`, which App calls after the
  document.
  - The group the next stroke on a surface joins (`inkActiveRef`, `{page,
    id}`). Leaving the page forgets it, so the next stroke starts a fresh
    group.
  - A new group is found through `blocksRef`, once a render has put its
    block in the tree.
  - `handleInkStroke(page, stroke, size, {label})` (the layers'
    `onStroke`) appends the stroke to that group as one `applyInk`.
    - `label` is the history entry's label the layer's `onStroke` passes
      (`finishStroke`'s: "ink stroke", "ink straight line", "ink shape").
      *Stamp line* passes "ink straight line".
    - A group no draft holds has its saved file loaded first, so a stroke
      never replaces strokes that have not arrived yet. Otherwise the
      stroke applies within the layer's call.
    - It starts a new group on the same surface when the current one would
      pass `GROUP_SPLIT_BYTES` (3 MB) or `MAX_STROKES` with the stroke.
    - On a sheet (`page` its id) the group's file is a canvas. A stroke in
      the last quarter of the last sheet adds the next one once
      (`notebook.js` `wantsSheetAfter`, then `addSheetAfter(id, {once:
      true})`; [notebooks.md](notebooks.md)).
    - A stroke while a held squeeze is open marks it used
      (`noteSqueezeUse()`).
  - `handleInkStrokes(page, strokes, size)` is `handleInkStroke` for
    several strokes at once (`size` the surface in points, for a fresh
    group's file).
    - They join the surface's current group when it can take them all
      under `MAX_STROKES` and `GROUP_SPLIT_BYTES`, else a new group takes
      them all (a paste is never split across groups). They go through
      `appendStrokes` as one entry, `T("ink paste")`.
    - A set no group could hold is refused with "Too much handwriting to
      paste into one note."
    - It returns `{id, ids}`, the group and the ids as they landed.
  - `handleHoldStatus({text, once})` (`onHoldStatus`) raises
    `undoableInkStatus(text)`, a `once` status at most once a session
    (`straightHintRef`).
- `ink/useInkSelection.js`: the lasso selection and the handwriting
  clipboard, `useInkSelection({focusedBlockId, readOnly, inkUi,
  blocksRef, inkOf, applyInk, flushInk, handleInkStrokes, pickInkTool,
  revealInkStrip, inkBySurface, pasteTarget, onBoxDeselect, showInNotes,
  transcribe, setStatus}) → {inkSelection, inkClip, clearInkSelection,
  restoreInkSelection, handleInkSelect, handleInkMoveSelection,
  deleteInkSelection, selectionStill, handleInkAction, copyInkSelection,
  pasteInk, pasteAtCentre, duplicateInkSelection, selectAllInk}`, which
  App calls after the strokes.
  - The lasso selection (`inkSelection`, `{page, items: [{id, ids}]}`).
    - `handleInkSelect(page, items)` (the layers' `onSelect`) sets it.
      When it holds strokes, it shows the strip with its options row
      closed, lets go of a selected text box or picture (`onBoxDeselect()`,
      which App points at both) and emits `ink.selected`.
    - The selection belongs to the lasso: arming another tool drops it,
      and so does leaving the page.
    - `clearInkSelection()` and `restoreInkSelection(selection)`, stable
      for App's life, are its commands for App and the other owners (an
      undo, a selected box or picture, Play, Esc; the end of a held E or
      squeeze).
    - `selectionStill(selection)` is a selection kept aside as it stands,
      without the strokes erased meanwhile, or null when none is left.
  - Each edit of the selection is one `applyInk` across its groups
    (`editInkSelection(edit, label)`, `edit(ink, ids) → ink`). The edits
    are the layers' `onMoveSelection` (`handleInkMoveSelection`, "ink
    move") and Delete (`deleteInkSelection`, "ink deletion", which lets go
    of the selection too).
  - **The handwriting clipboard** is `inkClipRef`, `{type: "gamma/ink",
    version: 2, space, box, strokes}`: the file's `space`, the selection's
    `inkBounds` box `{x1, y1, x2, y2}` in points and the raw stroke
    objects as the file holds them.
    - Copy and cut fill it and, best effort, write a `ClipboardItem` beside
      it, so another app or a later build can read it. That is
      `writeInkClipboard`: `text/html` a `<div data-gamma-ink>` whose text
      is that JSON, `text/plain` the JSON.
    - A browser that refuses the write loses nothing, since the ref is the
      one source a paste reads (reading the system clipboard back is not
      built).
    - The ref does not survive a reload. `inkClip` says whether it holds
      strokes, for the commands' guards and the layers' *Paste here*.
  - `handleInkAction(action, value)` (the layers' `onAction`) handles
    these, the selection's strokes, box and space coming from
    `selectedInk()`:
    - `copy`, and `cut` (copy, then the selected strokes removed as one
      entry, `T("ink cut")`, and the selection let go). Both raise
      `setStatus(tn("Copied {n} stroke.", "Copied {n} strokes.", n) |
      tn("Cut {n} stroke.", "Cut {n} strokes.", n), {label: t("Paste"),
      run: pasteAtCentre})`.
    - `flip` (`{axis}` → `flipStrokes` about the selection box's centre,
      `T("ink flip")`, the selection kept).
    - `split` (`T("ink split")`): the strokes leave their groups and a new
      ink block on the same surface takes them, positioned by
      `pdfPositionOf`. It is one entry whose changes name the old groups
      and the new block, which `applyInk` inserts, and undo removes it
      again.
      - The new block's strokes become the selection only when `applyInk`
        applied the entry, as the copies of a Duplicate and the strokes a
        merge moved do. The byte budget may refuse each of them.
    - `merge` (`T("ink merge")`): the selection's whole group is appended
      to the page's previous group (`inkBySurface`'s order, the nearest
      before it, else the first after) through `appendStrokes`, and
      emptied.
      - `flushInk` deletes the empty block when it has no caption or
        children. One that has them is refused before anything moves, with
        "Not merged: this note has a caption or notes under it."
      - A merge that would put the target past `MAX_STROKES` is refused
        with the byte budget's pill.
    - `reshape` (`{id, params}` → `reshapeInk`, the entry `T("ink shape
      edit")`).
    - `transcribe` ("Transcribe a selection", below).
  - `pasteInk(page, at, size)` makes the copies with `cloneStrokes(strokes,
    dx, dy)`, hands them to `handleInkStrokes`, selects them and arms the
    lasso.
    - `dx, dy` take the clipboard box's top-left to `at` and are then
      clamped so the box stays inside the surface. On a surface too small
      for it, its top-left corner stays on.
    - `pasteAtCentre()` is it with the box centred on the surface in view
      (`pasteTarget()`, `ink/useInkSurfaces.js`). The commands, the pill's
      *Paste* and the More menu's row all run it.
    - The page's `onPaste(page, pt, size)` (a held pen or mouse, a
      right-click) runs `pasteInk` at the point.
    - `duplicateInkSelection()` (Ctrl+D) sends
      `duplicateOffset(inkSelection.page)`, the offset the menu's Duplicate
      sends (`ink/surfaceScale.js`).
    - `selectAllInk()` takes the selection's surface, else the one in
      view.
  - The selection's `style` action: `handleInkAction("style", value)`
    routes each part to its edit, each one entry that goes through
    `applyInk`'s byte budget like any edit:
    - `value.brush` (present, `null` for the plain pen) to `restyleBrush`
      with the entry `T("ink style change")`;
    - `value.dash` (present, `null` for solid) to `restyleStrokes` with
      `T("ink line change")`;
    - the `fill` to `restyleStrokes` with `T("ink fill change")`;
    - a colour or a size to `restyleStrokes` with "ink color change" or
      "ink width change".
  - **Transcribe a selection** (`handleInkAction("transcribe")`,
    `transcribeInkSelection()`), its ink half. The chat half is
    `transcribe`'s, App's `askToTranscribe` (below).
    - In a shared page's view App passes none, and Transcribe only says
      "Transcribing needs the AI chat, which a shared page cannot use."
    - A selection whose strokes are whole groups flushes the page's drafts
      first (`flushInk()`, awaited, so the stored files hold what the
      screen shows). It then hands over the groups' blocks
      (`transcribe({blocks})`).
    - When a group's save has not landed (its draft still dirty after the
      flush, or its block not in the tree), its strokes go as chips, as
      below, instead.
    - Otherwise it makes one chip per group and hands them over
      (`transcribe({chips})`). A chip is `{kind: "ink", id: the block,
      page_id: the open page, space: the group's file space, strokes: the
      selected strokes as the draft holds them, text: tn("Selected
      handwriting · {n} stroke", "Selected handwriting · {n} strokes",
      n)}`.
- `ink/selectionEdits.js` (pure; `frontend/tests/selectionEdits.test.mjs`):
  what `useInkSelection` works out before it edits.
  - `selectedStrokes(sel, inkOf)` is `selectedInk()`, and
    `selectionStill(sel, inkOf)` the owner's `selectionStill`.
  - `selectionChanges(sel, inkOf, edit)` is the changes of
    `editInkSelection`.
  - `actionEdit(action, value, box)` gives each in-place action of
    `handleInkAction` its edit and its history label.
  - `duplicatePlan`, `splitPlan` and `mergePlan` give Duplicate, New note
    and Merge into note their changes and the strokes to select, or
    `{refused}` with the pill's words.
  - `selectAllItems` is select all, and `transcribeGroups` and `inkChips`
    are Transcribe's groups and chips.
  - `clipOf`, `pasteShift` and `centredOn` are the clipboard and where a
    paste lands.
- `ink/useInkRuler.js`: the ruler, `useInkRuler({focusedBlockId,
  readOnly, inkStale, pdfHidden, notebook, inkUi, inkVisible, inkPen,
  setInkRulerUnits, pasteTarget, openRulerRow, closeRulerRow,
  handleInkStroke, undoableInkStatus}) → {inkRuler, toggleInkRuler,
  hideInkRuler, clearInkRuler, inkRulerOptions, stampRulerLine,
  onRuler}`, which App calls after the selection.
  - `inkRuler` (`{surface, x, y, angle, length, snap}`, or null) is
    session state. It is gone when the strip closes (App's
    `closeInkStrip()` runs `clearInkRuler()`) and when the open page
    changes.
  - It also goes, its row with it (`hideInkRuler()`), when the viewer
    stops showing its surface: a PDF page's when the PDF is hidden or the
    notebook view opens, a sheet's when it closes (`inkRulerShown`).
  - `toggleInkRuler()` shows `defaultRuler` on the surface in view with
    its row open, or hides it (`hideInkRuler()`, the ruler's row with it).
    The surface is `pasteTarget()`'s, across `visiblePart(page, size)`,
    the part of its box inside its viewer and the window, in page points.
  - `inkRulerOptions({snap} | {units} | {hide})` is the row's (*Snap 15°*
    puts the angle on the grid at once). `onRuler` (the layers') sets
    `inkRuler` (null hides it).
  - `stampRulerLine()` draws a line along the ruler's middle between its
    ends with the armed preset (else the stylus pen).
    - It goes through `finishStroke`'s hold stage (a span of 0, `t0` the
      current time: `ruler.js` `stampLine`) and `handleInkStroke` on the
      ruler's surface (the size of the surface it was laid on, kept beside
      it).
    - It is one entry, then `undoableInkStatus(t("Line stamped along the
      ruler."))`.
- `ink/useInkZoom.js`: the zoom window, `useInkZoom({focusedBlockId,
  readOnly, isPhone, pdfHidden, notebook, inkZoomHeight, viewerWrapRef,
  pasteTarget, setStatus}) → {inkZoom, toggleInkZoom, inkZoomNewLine,
  changeInkZoom, handleInkZoom, clearInkZoom}`, which App calls after
  the ruler.
  - `inkZoom` (`{page, x, y, w, h, mag, line, x0}`, or null) is session
    state, gone when the strip closes (`closeInkStrip()` runs
    `clearInkZoom()`) and when the open page changes.
  - The window also closes when the PDF is hidden, the notebook view
    opens or the layout turns to a phone's.
  - `toggleInkZoom()` opens `openWindow` on the PDF page in view, or
    closes it.
    - The window's middle goes at the middle of what shows of the page
      above where the panel will stand (`visiblePart` less the panel's
      height). Its size comes from the scroller's width and
      `inkZoomHeight` less `ZOOM_CHROME`.
    - It does nothing on a phone, and on a sheet (or with no PDF page in
      view) says "The zoom window works on PDF pages."
  - `onZoom(page, patch)` (`handleInkZoom`) moves the window:
    - `moveTo` for `{at, size}`, the window going to that page;
    - `moveWindow` for `{x, y}` (both moves by hand set `x0`);
    - `{mag}` through `setMag`.
  - The panel's own buttons, its auto-advance and its view's size go
    through `changeInkZoom(update)`, and Enter through
    `inkZoomNewLine()`, all by `zoomWindow.js`.
  - The size of the window's page in points is kept beside it.
- `ink/useStripKeys.js`: the strip's keys, `useStripKeys({inkUi,
  inkSelection, inkVisible, inkTools, setInkTools, pickInkTool,
  toggleInkShape, closeInkOptions, toggleInkOptions, closeInkStrip,
  inkUndo, boxUndo, hasBox, onPicture, deleteInkSelection, selectionStill,
  clearInkSelection, restoreInkSelection, inkRuler, toggleInkRuler,
  inkZoom, toggleInkZoom, inkZoomNewLine, setStatus}) →
  {noteInkEraseHold}`, which App calls after the zoom window. `hasBox` is
  whether a text box or a picture is selected, and `onPicture` the Picture
  button's `pictures.pick` (null on a read-only page, where `I` does
  nothing).
  - While the strip is open (`inkUi.open`), a capture-phase `keydown` and
    `keyup` listener on the window hands each key to `stripKey` and does
    what it answers ([hotkeys.md](hotkeys.md) "Widget keys"). It passes
    the latest render's state (`inkKeysRef`, assigned every render).
  - A focused text field keeps its keys, but a keyup still reaches the
    handler.
  - `[` / `]` raise the width pill each time, at either end of the sizes
    too (`stripKeys.js` `stepWidth`; Shift+`[` / `]` `stepBrush`).
  - The handler takes `stripKey`'s `toggle` and `off` (`what`: `ruler`,
    `shape`, `zoom`) and `newLine`. Its state carries `ruler`, `zoom` and
    `onControl` (the target inside a button, a link, a menu row or a
    comment thread).
  - An open menu, a preset being dragged and a focused comment thread keep
    Escape from it.
  - **Hold-E**: the handler takes `stripKey`'s `eraser` action.
    - `down` arms the eraser as a tap does and keeps `inkHoldRef = {since,
      erased: false, selection, tool}` (the lasso selection and the tool
      armed before).
    - `onEraseEnd` marks `erased` (App's `handleInkEraseEnd` runs
      `noteInkEraseHold()`).
    - `up` with `held ≥ 300` and `erased` picks `tool` again. When that is
      the lasso, it restores what is left of the snapshot
      (`selectionStill`).
    - A shorter press, or one that erased nothing, leaves the eraser
      armed. A window `blur` clears the hold.
- `ink/usePencil.js`: the Apple Pencil under the iPad app
  ([ipad.md](ipad.md) "Gestures"), `usePencil({readOnly, takesInk,
  inkUi, inkVisible, previousTool, pickInkTool, applyPencilUi,
  inkSelection, selectionStill, restoreInkSelection, stripUndo,
  inkPencilTap, inkPencilSqueeze, inkHaptics}) → {hostHaptic,
  noteSqueezeUse}`. App calls it after the strip's keys, `takesInk`
  being whether the page takes ink (a PDF page or the notebook view
  shown, or sheets among the notes).
  - **The Pencil's gestures** (`pencilRef`, listening to the host's
    `pencil` and `squeeze` under `nativeHost().pencil`, on a page that
    takes ink).
    - Each goes through `pencilAction(message, {tools, pen, tap:
      inkPencilTap, squeeze: inkPencilSqueeze}, {tool, previous, options,
      busy: inkBusy(), squeeze: squeezeRef.current, now})`. It goes by way
      of `pencilActions.js` `pencilStep`, whose run list the hook plays.
    - `{pick}` runs `pickInkTool`, and `{ui}` patches `inkUi`.
    - `{undo}` runs `stripUndo(false)` (the strip's Undo: the stroke
      history, or the block history under `boxUndo`).
    - `{began}` opens `squeezeRef` (`{since, ignored, hold}`): a `pick` is
      armed, with the tool before and the lasso's selection kept in
      `hold`.
    - `{release}` picks back, and `stay` keeps the tool the squeeze armed.
      The selection is restored when that is the lasso, as
      `selectionStill(selection)` has it: without the strokes erased
      meanwhile, none when nothing is left.
    - An `ended` or `cancelled` closes `squeezeRef` whatever the answer.
    - An erase pass (App's `handleInkEraseEnd`) or a committed stroke
      (`handleInkStroke`) while a hold is open marks it `used`
      (`noteSqueezeUse()`, which answers whether one was open).
    - A host `squeeze` is taken only in its `began` and `cancelled`
      phases.
  - **Haptics** (`hostHaptic(kind, at)`, the page tools' `onHaptic`):
    `postToHost({type: "haptic", kind, at?})` when `nativeHost().haptics`
    and this browser's `inkHaptics` are on.
    - `tool` is for a gesture's pick that changed the armed tool (an
      `{ui}` or an `{undo}` plays nothing).
    - From the layer, `select` is for a pen lasso that selected (at its
      lift) and `snap` for a held shape (at the tip), both in viewport CSS
      px.
- `ink/inkCommands.js` (pure; `frontend/tests/inkCommands.test.mjs` runs
  it through `APP_COMMANDS`): `inkCommandContext({inkUi, inkTool,
  inkSelection, readOnly, inkClip}, {copyInkSelection, pasteAtCentre,
  duplicateInkSelection, selectAllInk})`. It is the fields App's command
  context carries for the handwriting commands (under `app/App.jsx`
  below).
- `markup/` ([text_boxes.md](text_boxes.md) "Client"):
  - `MarkupToolbar.jsx` `MarkupToolbar` is the strip. Its first row
    (`.pdfInkRow`) is five groups split by `.pdfInkSep`: the presets; the
    eraser, the lasso, the Text tool and the Picture button; the hand;
    **More** and close; and the history buttons last, whose labels follow
    its `blockHistory` flag.
    - The Picture button (`ImageIcon`, aria-label "Picture", title
      "Picture (I): add a photo or an image to the page") calls
      `onPicture()`, App's `pictures.pick`. Without `onPicture` (a
      read-only page) the strip draws no button.
    - More, the Tool menu and the Style picker's menu are one
      `useMenuTrigger()` (`"more"`, `"tool"`, `"style"`, one open at a
      time, below their buttons;
      [ui-design.md](ui-design.md#menus-and-submenus)). A press on the open
      menu's button closes it, and a press on another of the three buttons
      swaps the menus.
    - Each is a `ContextMenu` rendered **after** the `InkTooltips` element
      and opened on the press, never through `MenuButton` or `ActionMenu`,
      which open on the click inside the host.
    - An effect closes the Tool and Style menus when the preset's options
      row closes.
    - The More button (`ctlBtn`, `MoreIcon`, aria-label and title "More
      handwriting tools", `aria-haspopup="menu"`, `aria-expanded`,
      `data-guide="ink.more"`) lists `CommandMenuItem`s for `ink.paste`
      (titled "Copy or cut handwriting first" while greyed) and
      `ink.selectAll`, a `MenuDivider` and *Handwriting settings…*. The
      rows between the dividers are below ("The ruler, the Shapes tool and
      the zoom window").
    - *Handwriting settings…* is `SettingsIcon` and `onOpenSettings()`,
      which App wires to `openSettingsPane("handwriting")`. That is the
      settings alias (`settings/settingsNavigation.js`) for the Reading &
      editing pane, scrolled to the section's first row, *Reset presets*.
    - A preset's ⋯ (`ctlBtn`, `MoreIcon`, aria-label "Tool menu", title
      "Move, hide, reset or remove this tool", `aria-haspopup`,
      `aria-expanded`) lists these, each through `onChangeTools(list)` with
      the ids kept:
      - Move left and Move right, each `disabled` at its end of the visible
        row;
      - Duplicate and Hide (`EyeOffIcon`);
      - Hidden tools, a `SubMenuItem` of the `hidden` presets by their
        names (`disabled`, title "No hidden tools", with none);
      - Remove (`danger`), a divider and Reset this tool.
    - The strip's pills go through its `onStatus`: *Move left* / *Move
      right* and a drop say "Blue pen is now key 3." (or "Blue pen moved."
      past the ninth place), and *Hide* says "Blue pen hidden." with
      *Show*. `[` / `]` say "Blue pen · 2.8 pt." (`useStripKeys`).
    - The name is the preset's colour and kind in words, as the button's
      label spells them (`inkToolName(preset)`, which `MarkupToolbar.jsx`
      exports beside `moveTool(tools, id, to)`, the reorder a drop and Move
      left / right share).
    - The options rows (`.pdfInkSub`):
      - A preset's holds the three captioned sub-rows (`.inkOptRow` with an
        `.inkOptCaption`).
        - At 480 px and under the captions go, Style and Width wrap onto
          one line, the strip's `inkSizeBtn` dots narrow to 24 px and its
          `inkSegBtn` pills' padding to 6 px.
        - That `@media` block follows the strip's rules in `app.css`, which
          are as specific, so it wins.
      - Style, for a pen, is the **Style picker** (`InkStylePicker` in
        `ink/InkStylePicker.jsx`, since the selection menu uses it too and
        `ink/InkLayer.jsx` imports nothing from `markup/`).
        - It is a `ctlBtn inkStyleBtn` with `aria-haspopup="menu"`,
          `aria-expanded`, aria-label "Style: {brush}" naming the current
          one ("Style: Pen") and title "Pen style · Shift+[ ]".
        - It holds a `BrushPreview` glyph (a 28 × 10 SVG stroke drawn in
          that brush's look), the brush's name and a 14 px
          `ChevronDownIcon`.
      - Its button spreads the strip's `trigger("style")`
        (`InkStylePicker`'s `trigger` prop: its id, the press, the
        keyboard's click, `aria-haspopup` and `aria-expanded`). The button
        shows `modeActive` while its menu is open.
      - The menu, rendered after the `InkTooltips` element like the
        others, takes the same hook's `menu` prop (its place under the
        picker, the picker as `ignoreRef`, `onClose`).
        - It is `InkStyleMenu`, the `ContextMenu` named "Pen style" (an
          `aria-label` in place of the hook's `aria-labelledby`: the
          picker's name is the brush it shows). It holds five `MenuItem`s
          in the order Pen · Ballpoint · Brush · Pencil · Calligraphy, and
          stops a press in it from reaching the page.
        - Each is `role="menuitemradio"` with `aria-checked` on the current
          one and the `BrushPreview` as its icon (the calligraphy one drawn
          by `nibOutline` at 45°).
        - They are titled "Pen: width follows stylus pressure", "Ballpoint:
          an even line at every pressure", "Brush: a felt tip that swells
          with pressure and tapers at the ends", "Pencil: tilt widens the
          line, pressure darkens it", "Calligraphy: a flat nib, thick
          across it and thin along it".
        - A row calls `edit({brush})` with `'pen'` (which `normalizeTools`
          drops), `'monoline'`, `'brush'`, `'pencil'` or `'calligraphy'`,
          the brush alone.
        - A style change, by Shift+[ / ] as by the menu, writes the brush
          alone (a calligraphy pen's smoothing of 2 is `presetSmoothing`'s
          default, never stored).
      - A highlighter has its *Straight* trio in the picker's place.
      - Then come the **Line** trio and the *Smoothing* ladder:
        - the Line trio is `InkLineChoices` (`role="group"`, aria-label
          "Line"): *Solid* · *Dashed* · *Dotted* pills, each with a glyph
          of its line, titled "Solid line", "Dashed line: dashes two widths
          long", "Dotted line: a dot every two widths". It calls
          `edit({dash})` with `null`, `'dashed'`, `'dotted'`, for pens and
          highlighters both;
        - the *Smoothing* ladder is five `inkSizeBtn` dots for `stabilize`
          0–4, the pressed one the level the preset draws with
          (`presetSmoothing`, a calligraphy pen's 2 when it sets none).
      - Color is the `ColorChoices`, and Width the eight dots, Duplicate,
        Remove and ⋯.
      - The eraser's row has, after the sizes, the *Erases* trio and the
        *Back to last tool* toggle (`aria-pressed`, title "After erasing,
        return to the tool you had").
    - A preset button is `touch-action: none` and carries the drag
      (`usePresetDrag`).
      - A mouse past 6 px, or a touch or pen past `HOLD_MS` 500 ms from
        `shared/ui/press.js`, takes `setPointerCapture`, draws the
        `.inkDragGhost` and the `.inkDropBar` and says "Drop to reorder."
      - On release it calls `onChangeTools` with the moved list and
        `swallowClick`s the click.
      - Escape or a release outside the strip cancels, and a hold that
        never moved is the button's ordinary click.
    - Hidden presets are filtered out (`visibleTools`) before the row and
      the key lists see them.
    - **The ruler, the Shapes tool and the zoom window.** The second group
      shows after the Text tool, each only while on:
      - *Ruler* (`RulerIcon`, `ctlBtn inkToolBtn modeActive`,
        `aria-pressed="true"`, aria-label "Ruler", title "Ruler (R): write
        straight along its edges — tap for its options",
        `data-guide="ink.ruler"`), whose click is
        `onToggleOptions("ruler")`;
      - *Shapes* (`ShapesIcon`, the armed tool's button for the id
        `shape`, aria-label "Shapes", title "Shapes (S): drag to draw a
        shape — tap again for its options", `data-guide="ink.shape"`);
      - *Zoom window* (`ZoomWindowIcon`, aria-label "Zoom window", title
        "Zoom window (Z): write large in the panel below — tap to close"),
        whose click is `onToggleZoom()`.
    - The More menu's rows after its first divider:
      - *Ruler* (`MenuItem`, `RulerIcon`, `role="menuitemcheckbox"`,
        `aria-checked`, `keys="R"`, a trailing `CheckIcon` while on),
        which calls `onToggleRuler()`;
      - *Stamp line* (`StampLineIcon`), which calls `onStampLine()` and is
        disabled, titled "Show the ruler first", without a ruler;
      - *Shapes* (`ShapesIcon`, the same checkbox row, `keys="S"`), which
        calls `onToggleShape()`;
      - *Zoom window* (`ZoomWindowIcon`, checkbox, `keys="Z"`), which calls
        `onToggleZoom()`. It is disabled, titled "The zoom window works on
        PDF pages", without `zoomAvailable`, and not rendered under
        `phone`.
    - The ruler's row (`.pdfInkSub`, `data-ink-options="ruler"`, open while
      `options === "ruler"`):
      - `.inkRulerAngle` "Angle {deg}°";
      - the *Snap 15°* · *Free* pills (`role="group"`, aria-label "Ruler
        rotation"; titled "Snap 15°: the ruler turns in 15° steps" and
        "Free: the ruler turns smoothly; hold Shift to snap"), calling
        `onRulerOptions({snap})`;
      - *Stamp line* (`StampLineIcon`, title "Stamp line: a straight line
        along the ruler with the armed pen");
      - the *pt* · *cm* pills (`role="group"`, aria-label "Ruler units";
        titled "Ticks in points" and "Ticks in centimetres"), calling
        `onRulerOptions({units})`;
      - *Hide* (title "Hide the ruler (R)"), calling `onRulerOptions({hide:
        true})`.
    - The Shapes row (`data-ink-options="shape"`, the armed tool's row,
      `options === true` with `active === "shape"`):
      - the kind pills (`role="group"`, aria-label "Shape": *Line* ·
        *Arrow* · *Rectangle* · *Ellipse*, each with a glyph of its shape),
        calling `onShape({kind})`. They are titled "Line: drag from one end
        to the other", "Arrow: drag from the tail to the tip", "Rectangle:
        drag from corner to corner; Shift makes a square" and "Ellipse:
        drag across its box; Shift makes a circle".
      - `.inkShapeUses` "Uses: {color} pen · {size} pt", the `stylusPen`
        preset's colour in words and its width. Its title is "Shapes take
        the colour, width and line of the pen a stylus writes with".
      - *Fill* (`PaintBucketIcon`, `aria-pressed`, title "Fill: rectangles
        and ellipses get a light tint of the pen's colour"), calling
        `onShape({fill})`.
    - A calligraphy pen's Style row has, after the Style picker, the **Nib
      angle** group (`role="group"`, aria-label "Nib angle", title "Nib
      angle: the slant of the flat nib, −60° to 60°").
      - Two `ctlBtn`s with 16 px `MinusIcon` and `PlusIcon`,
        aria-labelled "Nib angle −15°" and "Nib angle +15°", stand around
        `.inkNibReadout` ("45°") for every pointer. Each is greyed at its
        end of the range.
      - A `NibPreview` glyph follows (the nib at the angle, `aria-hidden`),
        and each change is `edit({nib})`.
      - Under a host with `pencilRoll` the row adds *Lock angle*
        (`aria-pressed`, title "Lock angle: rolling Apple Pencil Pro does
        not turn the nib"), calling `edit({nibLock})`.
    - Its props for these: `ruler` (`useInkRuler`'s `inkRuler`, or null),
      `rulerUnits`, `onToggleRuler()`, `onRulerOptions({snap} | {units} |
      {hide: true})`, `onStampLine()`, `shapeKind`, `shapeFill`,
      `onShape({kind} | {fill})`, `onToggleShape()`, `stylusPen` (the
      preset a stylus writes with), `zoomOpen`, `zoomAvailable`, `phone`
      and `onToggleZoom()`.
    - `options` is `false`, `true` (the armed tool's row) or `"ruler"`, and
      `onToggleOptions(which)` takes `"ruler"` for the ruler's.
  - `MarkupLayers.jsx` `<MarkupLayers>` is the one place a surface's layers
    mount (`PdfPage`, the notebook view's sheet, `NoteSheet`). It maps the
    tools and the surface's marks onto `InkLayer`'s props.
    - A read-only surface gets no edit callbacks, and one with no ink
      mounts no layer unless peers are writing on it (`useHasWet`, "Wet
      strokes").
    - While the Text tool is armed the layer only draws.
    - The picture layer and the text-box layer mount before the ink
      layer, so they lie under it ([text_boxes.md](text_boxes.md)
      "Client").
    - It passes the layer `ruler` (`ink.ruler` when its `surface` is this
      surface, else null), `rulerUnits`, `zoom` (`ink.zoom` when its `page`
      is this surface, else null) and the actions `onRuler` and `onZoom`
      (the latter only while a window is open).
    - It takes `chrome` (true by default) and `onZoomStroke` for
      `InkLayer`. The zoom window's `false` mounts no text boxes or
      pictures and passes
      the layer no ruler, window box, `onZoom`, *Paste here*, finger
      chords, finger-scroll hint or `onInkFeatures`.
  - `useMarks` (same file) builds App's marks per surface: its ink groups,
    and the lasso selection and the flash when they are on it. Its text
    boxes and its pictures come with their selections and flashes the same
    way. A surface's slice is kept while its parts are unchanged, so a
    memoized page re-renders only for its own marks.
  - `PageTools.jsx` `PageToolsContext` carries the tools to every layer:
    `{readOnly, ink: {tool, penTool, penOnly, pressure, eraserMode,
    eraserSize, eraserFilter, drawHold, scribble, fingerUndo, lassoMode,
    hoverPreview}, text, actions}`.
    - `drawHold` and `scribble` are the account flags, `eraserFilter`
      `'all' | 'pen' | 'highlighter'`, `fingerUndo` `'off' | 'tap' |
      'double'`, and `hoverPreview` this browser's flag.
    - `ink` also carries `clip` (the clipboard holds strokes) and `keys`
      (`{copy, cut, duplicate}`: the chord labels the selection menu puts
      in its titles).
    - It carries `ruler` (`{surface, x, y, angle, length, snap}`, or null),
      `rulerUnits` (`'pt' | 'cm'`) and `zoom` (`useInkZoom`'s `inkZoom`,
      or null).
    - For the Shapes tool `ink.tool` is `{tool: "shape", kind, fill,
      style}`, `style` the stylus pen's look with brush `monoline`, as
      `toolStyle` gives it, smoothing and straightening left out.
    - The actions are ref-backed through `useStableActions`, so their
      identity never changes. They are:
      - the stroke, erase, select, action, move, jump and finger-scroll
        handlers (and the text boxes' and the pictures');
      - `onEraseAlong(page, path, r, {mode, pass})`, the eraser's path
        since its last flush ("Erasing" above);
      - `onEraseEnd()` (the tool eraser lifted);
      - `onEraseMany(page, [{gid, ids}])` (a scribble's targets, one entry
        across groups);
      - `onUndoGesture('undo' | 'redo')` and `onHoldStatus({text, once})`;
      - `onPaste(page, pt, {width, height})` (a held pen or mouse, or a
        right-click, asking for the clipboard at a page point, with the
        surface's size in points);
      - `onHaptic(kind, at)` (a snap or a pen lasso's selection, for the
        iPad host's haptics);
      - `onInkLive(page, msg)` (a chunk of the stroke in progress for the
        page socket, "Wet strokes" above);
      - `onRuler(next | null)` and `onZoom(page, {x, y} | {mag} | {at,
        size})`.
    - `onAction` takes `transcribe`.
    - The peers' strokes in progress are not marks: a layer reads them from
      `ink/inkLive.js` for its own surface ("Wet strokes" above).
    - `armedClasses(tools)` gives a surface's container its armed-tool
      classes (`inkArmed`, `inkTouchDraw`, `textArmed`).
- `app/App.jsx` composes the owners above and owns the rest of the state:
  - The glue between the owners:
    - `boxUndo`, the Text tool armed, a text box selected or a picture
      selected: Undo is the block history's;
    - `closeInkStrip()`, which the strip's three closers (its ✕, Esc, the
      viewer's pen button) call: the strip's `closeInkStripUi()`, then the
      ruler's `clearInkRuler()` and the zoom window's `clearInkZoom()`;
    - `handleInkEraseEnd()`, the page tools' `onEraseEnd`:
      `noteInkEraseHold()`, then, unless `noteSqueezeUse()` finds a held
      squeeze open, `inkEraserLifted()`;
    - the Pencil's `takesInk`.
  - The page tools (`pageActions`, the `pageTools` memo, `inkKeys`, the
    marks with `inkFlash`), and the jumps between the notes and the page
    (`showOnPage`, `showInNotes`).
  - The other prefs ([settings.md](settings.md) "Reading & editing"):
    - the eraser's mode and size, and the lasso mode;
    - the account's `inkDrawHold`, `inkScribbleErase`, `inkEraserReturn`
      and `inkEraserFilter`;
    - this browser's `inkFingerUndo`, `inkHoverPreview`, `inkPencilTap`,
      `inkPencilSqueeze` and `inkHaptics`;
    - for the Shapes tool, the ruler and the zoom window: `inkShapeKind`
      and `inkShapeFill` the account's, `inkRulerUnits` and
      `inkZoomHeight` this browser's.
  - Turning `inkScribbleErase` on from Settings shows a pill explaining the
    gesture once.
  - The commands (`appCommands.js`, group `GROUP_HANDWRITING`
    "Handwriting", after Library in `GROUPS`; [hotkeys.md](hotkeys.md)
    "Widget keys"):
    - `ink.copy` "Copy handwriting" Mod-c, `ink.cut` "Cut handwriting"
      Mod-x and `ink.duplicate` "Duplicate handwriting" Mod-d (12 css px on
      the selection's surface), when the lasso holds strokes
      (`c.inkSelected`);
    - `ink.paste` "Paste handwriting" Mod-v, when `c.inkOpen && c.inkTool
      && c.inkClip` and `window.getSelection().isCollapsed`
      (`textCollapsed()`);
    - `ink.selectAll` "Select all handwriting on this page" Mod-a, when
      `c.inkOpen && c.inkTool`, which selects every stroke of every group
      on the selection's surface, else the one in view, and arms the
      lasso.
  - All five are `edits: true`. A `run` handed a key event from a text
    field returns `false`, so the browser keeps its own copy and paste
    there.
  - `appCmdRef.current` carries `inkOpen`, `inkTool`, `inkSelected` and
    `inkClip` for the guards, and the five actions, from
    `ink/inkCommands.js` `inkCommandContext`.
  - Icons in `commandIcons.jsx`: `CopyIcon`, `ScissorsIcon`,
    `ClipboardIcon`, `DuplicateIcon`, `RectSelectIcon`, from `Icons.jsx`
    (as are the menu's `FlipHIcon` and `FlipVIcon`).
  - **Presets on the account**: `useProfileSync` reports, on a load's first
    pull of the account only, an account entry whose server value replaced
    a local one this browser made itself (`onReplaced({name, local})`).
    - That local value is neither the default nor the value as this
      browser last took it from an account or sent it. That value is kept
      for the browser in `gamma-profile-seen`, whichever account it was.
      It is written on each pull and each push the server confirmed, a
      keepalive push's once the page reads the answer.
    - Any other change applies silently.
    - For `inkTools` App raises "Use this browser's pens instead?" with
      *Use this browser's*, once a session. The question is logged and
      holds the pill's own `pens` channel for 8 s as a `question`, which
      ranks with final messages ([ui-design.md](ui-design.md#the-status-pill)
      "The status pill").
    - The action takes the pill down and sets `inkTools` to the local
      list through `setInkTools`, which pushes it.
  - **Transcribe a selection**, the chat half: `askToTranscribe({blocks} |
    {chips})`, which `transcribeInkSelection` in `ink/useInkSelection.js`
    calls. A group's *Transcribe with AI* in the ⋮⋮ menu is
    `askToTranscribe({blocks: [block]})`.
    - `{blocks}` attaches each block not attached yet, the oldest attached
      block making room (said on the pill), and asks "Transcribe this
      handwriting into its caption." once.
    - `{chips}` puts the chips on `chatNotes`, each replacing an earlier
      ink chip of its block.
    - When, with the ink chips already waiting, they are more than
      `MAX_INK_SELECTIONS` (8) groups or their JSON passes
      `MAX_INK_SELECTION_BYTES` (65 536) together, it raises "Too much
      handwriting selected to transcribe; select less." and sends nothing.
      Both caps are in `chat/passageBudget.js`, the server's caps.
    - Else it runs `showChat()` and `setChatAsk` "Transcribe the selected
      handwriting.".
    - The composer shows an ink chip as "Selected handwriting · 4 strokes"
      with a pen, without a preview.
    - ChatDock sends them as `ink_selections: [{page_id, block_id, space,
      strokes}]` ([ai.md](ai.md) "Pointing the chat at notes") and drops
      them once sent.
- With the strip open, Ctrl+Z is the stroke history (a capture-phase key
  handler, `useStripKeys`'s, so the page's block undo never sees it). With
  it closed, Ctrl+Z is the page's block history, which knows the group's
  block but not its strokes.
  - Text boxes and pictures are blocks: while the Text tool is armed, a
    box is selected or edited, or a picture is selected (App's `boxUndo`),
    the handler leaves Ctrl+Z to the block history. The strip's buttons
    step it (`undoBlocks`).
  - Escape goes to a selected or edited box, or to a selected picture.
  - An undo or redo applies its entry onto the group as it stands, by
    stroke id (`mergeInk`), so strokes someone else drew meanwhile stay.
    Two clients drawing into one group keep both drawings (see "Two
    writers, one group").
  - A focused note editor keeps the block history even with the strip
    open, and other text inputs keep their own undo. An empty ink history
    never falls through to block undo.
  - Undo and redo report the action and PDF page in the status pill
    (“Undone: ink width change (page 1).”).
  - The labels distinguish drawing, erasure, partial erasure, move,
    colour, width, duplicate, delete, paste, cut, flip, split, merge, a
    straight line, a shape, a shape edit, a style, a line and a fill
    change.

## Server

- `gamma/ink.py` computes no width and no opacity: it reads `w` and `o`.
  - It holds the pydantic schema (`extra="allow"` with the caps) and
    limits, the codec, `upgrade_v1` (the version 1 converter) and
    `read_upload`. `read_upload` is the parsed file behind a block's
    `ink_url`, None when unreadable: the exporters' one loader.
  - `dumps` writes the canonical bytes, equal to the client's
    `serializeInk`, so the same strokes dedup to one upload.
    `encode_points` rounds with `js_round`, `Math.round`'s twin.
  - `merge_ink` returns the file, `clean`, `lost_ours` and `returned`.
  - `shape_samples(kind, params, limit?)`: the samples a shape record is
    drawn as, `shapes.js` `shapeSamples` step for step.
    - It has its `densify` at 2 pt, `arrowPolyline` and round branch,
      `js_round` for `Math.round`, and `_js_hypot` for `Math.hypot` as V8
      and JavaScriptCore compute it. A side's sample count turns on the
      last bit of its length.
    - `tests/shared/inkshapes.json` `samples` holds both sides to the same
      cases.
    - A server writer of shape records (the Notability import) bakes `pts`
      with it.
    - Sin and cos are the platform's, and maths libraries round their last
      bit differently: the Windows CRT and V8 disagree on about 2.5 % of
      sines and cosines, sin(45°) among them.
    - So a turned rectangle, polygon or ellipse can come out a sample 0.01
      pt off, or a side whose length is a whole number of steps with one
      sample more or fewer. On Windows that is 157 of 40,000 random
      shapes, 149 of them turned ±45°.
    - The fixture's cases keep clear of that last bit.
  - `stroke_width` (`size × w`, else `size`: no law), `stroke_opacity`
    (`opacity × o`, else `opacity`) and `stroke_polyline` (variable-width
    polylines every renderer draws from, band 0 first).
  - `DASH` and `dash_pattern(stroke) → (on, off) | None` (the client's
    table).
    - `dash_runs(stroke)` gives the on-runs by arc length, boundary samples
      lerped in x, y, p, t, w and o, a and z from the sample before.
    - `dash_dots(stroke)` gives a dotted stroke's dots, one at each run's
      middle.
    - `tests/shared/inkdash.json` pins them against the client's
      `dashArray` / `dashRuns` / `dashDots`, and with them the run
      splitting the bands share.
  - `opacity_band(o)` and `stroke_bands(stroke)` are the pencil's shading:
    the stroke cut into runs of one opacity band, `o` quantised up to 0.33
    / 0.66 / 1, a sample lerped where `o` crosses an edge. A stroke without
    `o` is one run at band 1.
  - `stroke_outline(stroke, samples?, caps?)` is perfect-freehand 1.2.2's
    outline at the client's `outlineOptions` (`samples` a run of it),
    ported line for line. A test pins it to the library's output on a
    stroke that turns back on itself.
    - It is driven by pressure `w / 2`, so its half-width is `size × w /
      2`.
    - It runs at ten times the page's scale, since perfect-freehand drops
      every point within 3 units of a path's end and a dash or band run
      shorter than 3 pt would outline as a blot.
    - A run of two samples gets a midpoint first, since its two-point path
      reads their pressure as 0.5.
  - `pen_outline(stroke)` is the whole of a stroke without `o` as the
    client's `penOutline` draws it (`tests/shared/inkoutline.json` pins
    it). It is at the page's own scale, so a hook in its last 3 pt drops as
    on the screen, with perfect-freehand's own points between two samples.
  - The calligraphy nib, as the screen draws it: `NIB_HAIR` (0.12),
    `DEFAULT_NIB` (45), `nib_outline(samples, size, nib)` and
    `nib_shape(stroke)`.
    - `nib_outline` is the client's `nibOutline`, `woundPath` and
      `sweepSegment`, operation for operation, its pieces as polygons
      wound clockwise as seen.
    - `nib_shape` is `buildOutline`'s nib branch: the whole stroke or each
      dash run as a nib of its own, a dotted stroke's dots round at their
      drawn width.
    - `tests/shared/inknib.json` pins the paths.
  - The pencil's grain, as the screen draws it: `GRAIN_PX` (64, the tile's
    pixels a side), `GRAIN_UNITS` (32, the page units it spans),
    `grain_rgb(color)` and `grain_png(color)`.
    - `grain_rgb` is the client's `rgbOf`: `#rrggbb`, `#rgb` or
      `rgb[a](…)`, black for anything else.
    - `grain_png` is `grainUri`'s PNG byte for byte. The chunks before and
      after the palette are made once and nothing is kept per colour,
      since the colours are a writer's data.
    - `tests/shared/inkgrain.json` pins it.
  - `bounding_box` (over the raw integer arrays, no per-sample objects: the
    running sums of x, y and w, which `routers/ink.py` computes on every
    flush) and `pdf_position` (the shape is `gamma/highlights.py`'s).
  - The exporters draw a stroke one of five ways (`_drawn_as`):
    - a **highlighter** as its centreline at its size, multiplied onto what
      lies beneath;
    - a **calligraphy pen** without a shape as its nib (`nib_shape`);
    - a **dashed** stroke as one centreline at its mean drawn width and
      mean drawn opacity, round caps, the pattern its size's;
    - a **pencil** (a mouse pencil without `o` too) or a **translucent
      pen** (`o`, or an opacity under 1) as a filled outline per opacity
      band;
    - an **opaque solid pen** (and any tool this code does not know) as one
      round-capped segment per sample pair at that pair's baked width.
  - Each band is one path painted once, so it never darkens where it
    overlaps itself, as stroked segments under an alpha would, and a
    pencil's grain is never painted twice.
  - Each run is round only at the stroke's own ends and flat where it meets
    the next (the client's `runOutline`), so two bands meet without
    overlapping. A stroke without `o` is one band, its whole outline
    (`pen_outline`).
  - A pen with the pencil brush, a dashed one too, is painted with its
    grain (`_grained`). A highlighter or an unknown tool never is.
  - Each draws at the opacity the screen does (`drawn_alpha`): its own
    times its colour's alpha, as the screen fills an `rgba()` colour under
    `fill-opacity`. A pencil's is the exception: its grain tile takes the
    colour's r, g and b alone.
  - The `/Ink` annotation's `/CA` is the same.
  - `to_svg` writes them as:
    - a `<path>` with `stroke-opacity` and `mix-blend-mode: multiply`;
    - one `<path>` of the nib's pieces whose `d` is the screen's own string
      (`_fixed` writes the digits of `toFixed(2)`);
    - a `<path>` with `stroke-dasharray="on off"`;
    - a `<path>` per band with `fill-opacity` band × opacity;
    - `<g>` of segments.
  - A pencil's SVG paint is `url(#inkGrain-<rrggbb>)`. The `<defs>` hold
    one `<pattern patternUnits="userSpaceOnUse">` per pencil colour,
    `GRAIN_UNITS` page units square from the page's corner whatever the
    crop, holding the tile as an `<image>` (`preserveAspectRatio="none"`),
    as the layer draws it.
  - So the SVG needs a renderer with `<pattern>` and data-URI `<image>`
    support. Every browser, Inkscape and librsvg draw it as the screen
    does, and one without patterns (MuPDF's SVG reader) fills a pencil
    black.
  - `pdf_path_ops(ink, to_pdf, scale, band?, frame?)` serves the
    notes-as-PDF writer, the notebook PDF and the AI's page picture. It
    answers `b""` when nothing is left.
    - `band` keeps one band, for a writer drawing several groups band by
      band. `frame` is the matrix the ops are drawn under in the page's
      default space, identity by default.
    - It writes the highlighter under `/GSinkM<α × 1000>` (`/CA`, `/ca` and
      `/BM /Multiply`), and the nib's pieces as `m … l … h` subpaths (a dot
      as four cubics) filled once with `f`.
    - It writes the dash between `[on off] 0 d` and `[] 0 d` (scaled with
      the drawing), each band as cubic outlines filled once, and the
      segments as stroked lines, each at its pair's mean baked width.
    - A nib or a band below full opacity is filled under `/GSink<α ×
      1000>` (`/CA`, `/ca`).
    - A graphics state's name carries it, so `pdf_path_resources(ops)`
      declares every state some ops set from the ops alone (None when they
      set none). The writers embedding them put it in the page's
      `/ExtGState`.
  - A pencil paints with the pattern
    `/Grain<rrggbb>_<a>_<b>_<c>_<d>_<e>_<f>` (`/Pattern cs … scn`, and
    `CS … SCN` for a dashed one's stroke) under its band's `/GSink`.
    - The name carries the colour and the pattern's matrix
      (`_grain_matrix`: `to_pdf`, then `frame`, its y turned). So a cell is
      `GRAIN_UNITS` page units from the page's corner on a turned, cropped
      or flipped page too.
    - A pattern is placed in the page's default space, not the current
      transformation's, hence `frame`.
  - `GrainPatterns(writer).resources(ops)` is the `/Pattern` dictionary for
    every grain some ops paint with, read from the names (None when they
    paint none).
    - A document holds one soft mask (the tile's alpha, DeviceGray) and one
      64 × 64 DeviceRGB image per colour with that `/SMask` and
      `/Interpolate`. The image is smoothed when magnified, as on the
      screen (pdf.js draws it in blocks without).
    - It holds one coloured tiling pattern per colour and matrix, which its
      pages share (`PatternType` 1, `PaintType` 1, `TilingType` 1;
      `/BBox`, `/XStep` and `/YStep` 32; the cell `q 32 0 0 32 0 0 cm
      /Tile Do Q`).
    - `notebook_pdf` passes the frame `(1, 0, 0, -1, 0, h)` and
      `pdf_document` `(1, 0, 0, -1, 0, PAGE_H)`, each with one
      `GrainPatterns` per document.
    - `pdf_export._stamp_ink` keeps the identity, since the strokes follow
      the page's own content wrapped in `q … Q`. It merges the patterns
      into the page's own `/Pattern`.
  - For the `/Ink` interchange: `ink_buckets` (keyed by tool, colour,
    size, opacity) and `from_pdf_ink`.
    - A foreign `/Ink` whose `/BS` has `/S /D` imports as `dash:
      "dashed"`, whatever its `/D` array, since the pattern follows the
      width.
    - `from_pdf_ink` raises `InkError` for a coordinate past ±10 000 000 pt
      (`MAX_XY`), infinite or not a number, so the importer skips that
      annotation as any unreadable one.
    - `upgrade_v1` clamps an integer pressure as it is, never through a
      float. So a version 1 `/GammaInk` holding one past the largest double
      falls back to the `/InkList` like any payload that does not parse.
- `routers/collab.py`: the page socket relays a client's `{t: "ink-live",
  …}` to the room's other peers as it came, with `client` set to the
  sender's, when `collab.ink_live` admits it.
  - It admits it when the sender `can_edit`, `id` is a string, `samples`
    holds at most 64 entries and the relayed message is at most 16 KB
    ([collab.md](collab.md) "presence").
  - Nothing is stored, logged or replayed to a later joiner
    (`backend/tests/test_collab.py`).
- `POST /api/upload-ink` (`routers/ink.py`): the file as the JSON body,
  validated, stored as it came as `<hash of the bytes>.ink` with the usual
  quota check → `{url, size, strokes, bbox, pdf_position,
  already_existed}`.
  - A version 1 file is refused with `400 "update Gamma"`.
  - `POST /api/upload-file`, the route a mirror pushes files by, stores an
    `.ink` only when it is a valid version 2 drawing. Both routes refuse a
    bad file through `parse_upload` (`routers/ink.py`).
  - Editors and edit shares may upload (`require_ws_writer`).
  - `.ink` is in `storage.FILE_MEDIA_TYPES` (`application/json`), so `GET
    /api/uploads/<hash>.ink` is the ordinary upload route with its share
    scoping and cache headers.
- Interchange ([import_export.md](import_export.md),
  [pdf_typesetting.md](pdf_typesetting.md)):
  - The annotated PDF writes one `/Ink` per look bucket (colour × tool ×
    size × opacity) with `/InkList` in user space and the mean drawn width
    as `/BS /W` (from `w`).
    - The note goes on the first.
    - Each carries an `/NM` Zotero needs to import it as ink (the Zotero
      export's PDF copies carry it too), and a private `/GammaInk` key with
      the bucket's v2 strokes verbatim. So a Gamma re-import keeps
      pressure, time and every key.
    - Every group's band-0 buckets go before any band-1 one in a page's
      `/Annots` (`annotate_pdf` sorts a page's annotations by band,
      stably, since `/Ink` has no blend mode). The note carrier and the
      Zotero `/NM` keep the first-seen bucket index.
  - A bucket whose first stroke is dashed writes `/BS /S /D /D [on off]`.
    A bucket mixing dashes writes that first one's: `/GammaInk` carries
    the truth, and the dash is not part of the bucket key, so re-exports
    keep their `/NM` set.
  - The embedded-annotation importer reads `/Ink` (Gamma's or anyone's)
    into ink blocks, converting a version 1 `/GammaInk` payload, and
    strips them like the other types.
  - The Markdown export writes `![Handwriting (p.N)](assets/<hash>.svg)`
    with the SVG generated into the zip.
  - The notes-as-PDF document draws the strokes as vectors under a
    "handwriting, p. N" line, and the notebook PDF on their sheet. Every
    group's band 0 goes before any group's band 1, and each page declares
    the graphics states its ink sets and the grain patterns it paints
    with.
  - The agent's `read_block` outline labels an ink block "handwriting on
    p. N, K strokes" before its caption.
- The Notability import ([import_export.md](import_export.md#notability-notes),
  `gamma/notability_map.py`) writes v2 files of its own.
  - Notability keeps fitted Bézier chains, so each curve is flattened in
    page points to samples at most a point apart and within 0.05 pt of the
    curve, then decimated as `finishStroke.js` does. A point apart is the
    client's input spacing: sparser ones would let the pen outline's
    streamline round a corner.
  - `w` comes from Notability's fractional widths, and raw force and tilt
    become `p`, `a` and `z` only where they are real.
  - There is no `t` or `t0`: the note stores no sample times.
  - A page's strokes are split into groups by `ink.group_files`, the
    client's rule (`GROUP_SPLIT_BYTES`, `MAX_STROKES`, `MAX_SAMPLES`).
    Stroke ids are `n0`, `n1`, … in each file as `from_pdf_ink` numbers
    them, so the same note makes the same files.
  - Shapes keep their `shape` records, sampled as `shapes.js` draws their
    params (`ink.shape_samples`), and a sticky note is a filled `rect` in
    band 0.
- The Notability export ([import_export.md](import_export.md#notability-export),
  `gamma/notability_export.py`) writes each stroke as a straight Bézier
  chain whose anchors are its samples, each with its `w`, `p` and `a`/`z`,
  under a UUID the import knows (`notability.GAMMA_UUID`), so the import
  reads the samples back exactly; the stroke verbatim (its time, a
  pencil's `o`, every key) rides in the note's private copy, which the
  import takes while the curve still has those samples.
- `gamma/ink_view.py`: the picture the chat sees of a group (`view_ink`,
  and an attached handwriting block), drawn where it was written.
  - A sheet goes through `notebook_pdf`, and a PDF page through
    `pdf_export.page_with_ink(src, page_no, inks)`.
  - There every stroke is stamped through `pdf_path_ops` into the copied
    page's content, every group's band 0 before any group's band 1.
  - `_stamp_ink` wraps the page's own content in `q … Q` and appends the
    strokes after it, mapped by `page_frame` like the annotated export. It
    adds the states they set to the page's `/ExtGState` and the grain's
    patterns to its `/Pattern`.
  - The pens show their baked widths, tapers, dashes, the pencil's shades
    in its grain and the calligraphy nib.
  - The highlighters are multiplied onto the page, so the text under them
    stays as dark as it was, and they stay under the ink. `/Ink`
    annotations (drawn over the page's content) could not do that.
  - MuPDF rasterizes the sheet's PDF and the stamped page
    ([ai_tools.md](ai_tools.md) "view_ink").
  - `backend/tests/test_ai_tools_ink.py` renders text under a highlighter
    and asserts every glyph pixel stays dark, and a pen crossing a
    highlighter drawn after it and asserts the pen's pure colour.
  - It checks a pencil's three shades through its grain, the grain's place
    on turned and cropped pages, a calligraphy stroke's slanted ends and
    the dashes of a dashed stroke.
- **Calligraphy and fills on the server** (`gamma/ink.py`): `_GEOM_KEYS`
  holds `nib` beside `brush` ("Two writers, one group";
  `tests/shared/inkmerge.json` pins it).
  - `bounding_box` pads a calligraphy stroke without a `shape` by `size ×
    NIB_REACH / 2` at every sample (hypot(1, 0.12): the nib's corners), or
    its drawn half-width where that is wider. It does as `strokeBounds`
    does, so a merged file's box and the AI's crop agree with the
    client's.
  - The exporters draw a calligraphy stroke without a shape with its nib
    (`nib_shape`) and one with a shape as an even line, as the screen
    does.
  - A filled closed shape's fill is drawn first, as the closed polygon
    through its samples, in the stroke's band (`fill_of`, over
    `CLOSED_SHAPES`).
    - `to_svg` writes a `<path d="… Z" fill="rgb(…)" fill-opacity="α"/>`
      before the stroke's own elements.
    - `pdf_path_ops` writes the polygon filled with `f` in the fill's
      colour (`r g b rg`) under `/GSink<α × 1000>` (none at 1) before the
      outline.
    - That is a fill of its own rather than `B`, since a pen's outline is
      drawn as segments or bands, never as that polygon stroked.
  - A fill on a stroke that is no closed shape is not drawn.
  - The annotated PDF stays /Ink-pure: the outline is in its bucket's
    `/Ink` like any stroke and the fill only in `/GammaInk`'s strokes.
  - No `/Square`, `/Circle` or `/Polygon` is written for ink, and a Gamma
    import brings the fill back from `/GammaInk`.
- **A selection for the chat** (`routers/ai.py`, `ai_context.py`,
  `ink_view.py`; [ai.md](ai.md) "Pointing the chat at notes"):
  - `AIChatRequest.ink_selections` takes at most 8 entries
    (`MAX_INK_SELECTIONS`) and 64 KB of compact JSON together
    (`MAX_INK_SELECTION_BYTES`, `ink_selection_bytes`; a 422 past either).
  - `ai_context.request_ink_selections(payload)` keeps each `{page_id,
    block_id, space, strokes}` whose `page_id` is one of the request's
    context pages and whose strokes parse as a version 2 file in that
    space (`ink.parse_ink`). Anything else is dropped, with a log line.
  - `notes_focus_section` lists them under "Handwriting the user selected
    for this message (part of the note block in brackets):", each `-
    [block_id] (selected handwriting on p. N, K strokes)`
    (`selection_label`; "on a page of paper" for a canvas space).
    - The group's caption follows when its block is saved on that page (a
      caption of up to 500 characters counts as read, for an `edit_block`
      replace).
    - "(a picture of it is attached to the message)" follows when its
      picture went, labelled "The selected handwriting of block [id] on
      PDF page N".
    - After the list comes "Each picture holds only the selected strokes,
      saved or not: transcribe exactly those, not the rest of the block's
      handwriting."
    - The agent's prompt adds that a selection is read from its picture,
      not with `view_ink`, which shows the whole block as it was saved
      (`ai_tools.agent_system`).
    - A message whose only pointer into the notes is a selection still
      gets the section.
  - The picture is `ink_view.picture(ws, conn, block_id, page_id,
    ink=file)`: the inline file drawn instead of the block's stored one.
    - So the block need not hold a file, nor be saved yet, and the group's
      other strokes stay out.
    - It is drawn on PDF page `space.page` of the page's PDF
      (`page_with_ink`, even where the PDF still embeds the group), or on
      the sheet holding the block, else on blank paper of the space's
      size.
    - It is cropped to the selected strokes (`crop_box`), within the
      request's picture budget as an attached handwriting block's is
      (group `ink`).

## Not built yet

- The calligraphy nib's pressure: the screen and the exports draw the nib
  at the stroke's size, and the pressure's 0.85 to 1.15 is in `w` alone.
- On the iPad shell's native live line, the pencil's grain, a dashed
  pressure line's widths and the calligraphy nib's flat ends: it draws
  them untextured, even and round until the web's stroke replaces it.
- Live co-drawing of a stroke the iPad shell captures: its samples reach
  the page only at the lift, so peers see it when its file lands.
- Reading handwriting back from the system clipboard: copy writes a
  `gamma/ink` item for other apps, but a paste reads only this tab's own
  clipboard, so nothing pastes across tabs.
- The zoom window on sheets of paper and past a page's bottom: it covers
  PDF pages, and New line stops at the last line of a page.
- The ruler and the page's text boxes inside the zoom window: it shows the
  page's pixels and its handwriting.
- A pressure brush's widths along the ruler: a ruler line is even, as a
  held one is.
- Triangles and polygons from the Shapes tool (draw-and-hold makes them),
  and handles on a `curve`.
- A snap of straight highlights to the PDF's text lines, the gap left
  from the Notability comparison in the research note.
- Xournal++ `.xopp` import.
- From a Notability `.ntb`: its recordings, a shape's fill and arrowhead,
  which no bundle seen yet shows
  ([research/notability-format.md](../research/notability-format.md)).
- Strokes placed on a recording the Notability import brings: the note
  times a stroke as an event, not its samples.
- Handwriting in the Obsidian vault export, which writes an ink block's
  caption only.
- A replay of erasures and edits: it shows the drawing as it is, in the
  order it was written. Audio recording to go with the replay is
  [recording.md](recording.md).

The broader interaction survey is
[handwriting-interactions.md](../research/handwriting-interactions.md).
