# Handwriting (ink) annotations

Draw on a PDF page with a stylus, mouse or finger; the strokes become a
block in the page's notes. The survey behind the shape, and how Notability
does the same things, is in
[research/handwriting.md](../research/handwriting.md). Code:
`gamma/ink.py` + `gamma/routers/ink.py` (server), `frontend/src/ink/ink.js`,
`ink/inkStore.js`, `ink/InkLayer.jsx`, `ink/inkInput.js` (client), with the
tool strip and the page plumbing it shares with text boxes in
`markup/MarkupToolbar.jsx`, `markup/MarkupLayers.jsx` and
`markup/PageTools.jsx`; tests `backend/tests/test_ink.py`,
`frontend/tests/ink.test.mjs`, `frontend/tests/inkInput.test.mjs`,
e2e `tests/e2e/scenarios/ink.mjs` and `inkEditing.mjs`. The same files are
written on notebook sheets ([notebooks.md](notebooks.md)) and by the iPad
app ([ipad.md](ipad.md)). Typed text on a page is
[text_boxes.md](text_boxes.md).

## What the user sees

- The pen button in the viewer's zoom column opens the **tool strip** at
  the top of the page, laid out like Notability's: a row of **tool
  presets** — each a pen or a highlighter with its own colour and width,
  shown as the icon over a colour bar (four pens and three highlighters
  to start) — then the eraser, the lasso, the Text tool (typed text
  boxes, [text_boxes.md](text_boxes.md)), a hand (nothing armed: scroll
  and select text), close, and Undo / Redo. Its accessible name is
  "Markup tools". One tap arms a tool;
  **tapping the armed tool again opens its options row** under the strip.
  For a preset that row is the palette (14 pen / 8 highlighter colours
  plus a custom colour through the browser's picker), eight widths as
  dots, *Duplicate* (a copy right after it, armed and still open for
  editing) and *Remove*; the change applies to that preset, so the row is
  the user's own set of pens (up to 12, kept in localStorage,
  `gamma-ink-tools`). Colours are named in words, never hex codes: a
  preset reads "Pink highlighter · 14 pt · key 7", and each swatch is
  titled and labelled by its name (`INK_COLOR_NAMES` in `InkLayer.jsx`). A
  custom colour takes the name of the nearest palette colour
  (`nearestInkColor` in `ink.js`, redmean distance). Keys while the strip
  is open: `1`–`9` arm the preset
  at that position, `P` / `H` step through the pens / highlighters, `E`
  `L` `T` `V` the eraser / lasso / Text tool / hand, `Esc`, `Delete` (the
  lasso selection), and **`Ctrl+Z` / `Ctrl+Shift+Z` step the strokes**
  (each stroke or selection edit is one entry; the history is per visit
  of the page). While the Text tool is armed or a text box is selected or
  edited, `Ctrl+Z` is left to the block history, and `Esc` to a selected
  or edited box.
  Opening the strip arms the last pen used.
- The **eraser**'s options row: *whole strokes* removes anything it
  touches, *partial* cuts through them (the pieces on either side become
  their own strokes), and three sizes. The
  **lasso**'s row: *freeform* circles strokes (more than half their
  samples inside), *box* drags a rectangle; the dashed box then moves by
  dragging and deletes with `Delete`. Both work across groups on the page.
- **Tap existing ink to select it.** With *Draws with: Pen only* (the `inkPenOnly` preference), a finger
  tap or a 450 ms stationary hold selects the nearest stroke (10 CSS px hit
  tolerance, topmost wins). A mouse click in Hand mode and a short tap with
  the lasso select the same way. A swipe still scrolls: more than 8 CSS px
  of movement, a second contact, a pen contact or a cancel drops the pending
  selection. With finger drawing on, armed tools draw at once; use Hand to
  tap-select.
- The **selection menu** opens after a tap or a lasso: Color, Width,
  Duplicate, Select note (every stroke of the selected ink blocks), Show
  note (jump to the notes pane) and Delete. Edits apply at once and keep
  pressure and time; a mixed pen/highlighter selection gets a width row per
  kind. Duplicate offsets fresh-id copies by 12 screen pixels and selects
  them; a full group refuses. One action across several blocks is one undo
  entry. The menu follows scrolling and resizing, flips above or below the
  selection and hides while the selection is off-screen. It keeps inside
  the scroller the ink is seen through: the PDF viewer, the notebook
  view, or the notes for a sheet among them, and below the strip when the
  strip floats over the top of that scroller. A text box's menu is placed
  the same way (`useSelectionMenuAnchor`). A blank tap or Escape
  dismisses it.
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
- **A stylus draws right away** even with the strip closed (Settings →
  Reading & editing → Handwriting; on by default), with the last pen
  preset armed on the strip. **Fingers never draw**
  when *Draws with* is *Pen only* (`inkPenOnly`, default on touch screens): they keep
  scrolling and pinch-zooming. The first finger that scrolls a page while
  a tool is armed says so on the status pill, "Fingers scroll while a pen
  draws", with **Draw with finger** (which turns `inkPenOnly` off): once a
  session, and never after a pen has drawn. The pen's eraser end and
  barrel button erase.
- Strokes on one page join the **current group** until *New group*, a
  stroke on another page, or leaving the page. A group is one block in the
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
  they were written.
- **Transcribe with AI**, in a group's ⋮⋮ menu, attaches the block to the
  chat and asks for its text in the caption ([ai_tools.md](ai_tools.md)
  "view_ink").
- A group erased empty deletes its block, and undo brings it back. A
  group whose block holds a caption or notes keeps its block, with an
  empty drawing, so erasing strokes never deletes text.
- Read-only views (workspace viewers, view shares) show ink without tools;
  edit shares draw.
- On an iPad this layer runs in Safari or the web app installed to the
  home screen. The iPad app captures the Pencil natively and writes the
  same files ([ipad.md](ipad.md) "Ink on the iPad").

## Model

Three brushes, no textures: pressure-sensitive pen, monoline and
highlighter. A pen preset's options switch between Pen and Monoline; the
style survives preset duplication and reload. Monoline shares the
smooth outline renderer with pressure-based thinning disabled. Pressure and
timing stay in the samples. Live canvas, retained SVG, eraser geometry and
server exports all honor the style; partial erasing preserves it in each
surviving piece.

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

## The stroke file (`gamma-ink` v1)

Plain JSON (`application/json`), one per group:

```json
{"format": "gamma-ink", "version": 1,
 "space": {"kind": "pdf-page", "page": 3, "width": 612, "height": 792},
 "strokes": [{"id": "k7Qm2x", "tool": "pen", "color": "#1f1f1f", "size": 1.6,
              "opacity": 1, "pen": true, "t0": 1757760000000,
              "ch": "xypt", "pts": [12040, 30512, 620, 0, 18, -3, 700, 8]}]}
```

- `space`: `page` is 1-based; `width`/`height` the page as displayed at
  scale 1 (pdf.js viewport: points, origin top-left, y down, rotation
  applied) — the same frame highlight rects normalise to and the frame the
  PDF writers map to user space. `{kind: "canvas", width, height}` is a
  sheet of paper: points from its top-left corner, the sheet's size when
  the group was drawn ([notebooks.md](notebooks.md)).
- `ch` names the channels of each sample, InkML-style: `x` `y` always, then
  any of `p` pressure, `t` time, `a` altitude, `z` azimuth. `pts` is one
  flat integer array: x/y in 1/100 pt and t in ms are deltas after the
  first sample, p is 0..1000, a/z degrees. ~300 bytes per stroke.
- `tool`: `pen` (pressure-shaped outline) or `highlighter` (constant width,
  multiplied onto the page). `pen: false` marks mouse/finger strokes (no
  real pressure, drawn even). `size` is the nominal diameter in pt; drawn
  width = `size × (1 + 0.5 × (p − 0.5))` for a real pen. `t0` is wall-clock
  ms of the first sample — a client without timing omits `t`/`t0` rather
  than inventing them. It is an integer: the iPad rounds its clock. The
  replay reads both (see Client).
- Limits (`gamma/ink.py`, enforced on upload): 5 000 strokes, 500 000
  samples, 4 MB, finite numbers, unique stroke ids.

For `tool: "pen"`, optional `brush: "monoline"` selects constant width.
Omitting `brush` keeps the pressure behavior and serialized form;
highlighters do not accept it. Older servers reject `brush`
(`extra="forbid"`). The embedded Gamma payload in annotated PDF exports
retains the brush for re-import.

The codec lives twice by design (`ink.py` `decode_stroke`/`encode_points`,
`ink/ink.js` `decodeStroke`/`encodeStroke`); the two test files pin the same
sample bytes. The iPad runs `ink.js` itself (in JavaScriptCore), so it has
no third copy.

**The bytes of an upload** are the client's: `ink.js serializeInk` (keys
sorted at every level, no whitespace, empty keys dropped). The server
validates an upload and stores it as it came, named by the hash of those
bytes. So the same strokes from a browser and the iPad are one file, and
a client can name a file by its hash before the server has it (the
iPad's replica). The server writes files of its own with `ink.dumps`
(sorted keys) for a merge or an import.

## Two writers, one group

A group's drawing merges like a block's text ([collab.md](collab.md)
"same-block merge"), with strokes in place of spans. Stroke ids survive
every edit, so they play the part text offsets play. A write names the
file its strokes were drawn onto (`base_props: {ink_url}` on the op,
`base_properties` on `PUT /blocks/{id}`). When the stored `ink_url` is
another one, meaning someone else saved meanwhile, the server merges
instead of replacing (`ops._Batch.merge_ink`, `ink.merge_ink`):

- Where the stored drawing left a stroke as the base had it, the
  writer's change applies in place: a restyle, a move, an erasure.
- A stroke the writer added goes after the stroke before it that the
  result keeps, behind strokes the other side added there (theirs comes
  first, as with text).
- A stroke both changed keeps the stored version. A stroke one side
  changed survives the other's erasure (an edit beats a delete). Either
  makes the merge unclean.
- A base file that is gone merges as a union by stroke id. A file that
  cannot be read, or a result over the budgets, leaves the write as sent
  (last writer wins).

The merged file is stored (`ink.dumps`) only when it is new. One stored
already stays: the purge deletes only under the write lock the batch
holds. The applied op names the merged file, with its stroke count and
box. Merging a change that already landed adds nothing, so a mirror
resends a lost ink push as it is (sync_engine `_unlanded`). A merge that
changes nothing returns the side it equals, so a resend mints no copy.

The same function runs where a client applies another side's drawing
itself:

- the desktop mirror, through its local server's op path (its diffs
  carry `base_props`, sync_tree `_set_op`);
- the iPad's replica (`ink.js mergeInk` in `replica/tree.js applyLocal`);
- undo and redo in the browser and on the iPad, which apply an entry's
  change onto the group as it is now, so strokes someone else drew
  meanwhile stay.

`tests/shared/inkmerge.json` pins the Python and JavaScript versions to
the same cases. Keys other than `ink_url` stay last-writer-wins, except a
text box's `text_box`, merged key by key ([text_boxes.md](text_boxes.md)
"Merge").

## Client

- `ink/ink.js` (pure): the codec, bounds (`strokeBounds`, `inkBounds`,
  `boundsOf`, `unionBox`), `pdfPositionOf`, the stroke edits and the
  rendering. `hitStrokes` is the whole-stroke eraser's test; `eraseAt` the
  partial eraser, which re-encodes the surviving runs as new strokes;
  `translateStrokes` only touches the first sample's two absolute integers;
  `transformStrokes` scales/rotates selected XY samples around a shared
  origin without changing the other encoded channels;
  `strokesInLasso` picks strokes with more than half their samples inside
  the polygon. A pen stroke renders as perfect-freehand's outline in one
  filled SVG path (page units; the layer's `viewBox` does the zoom), a
  highlighter as a stroked polyline with `mix-blend-mode: multiply`. Paths
  and decoded samples are cached per stroke object.
  `nearestInkStroke` resolves a touch to the nearest stroke edge (topmost
  stroke wins ties); `restyleStrokes` changes selected color/width, returning
  the original object for a no-op; `duplicateStrokes` preserves original
  samples and channels while assigning unique IDs to translated copies.
- The replay (`ink.js`, pure, so the iPad runs it too):
  - `inkTimeline(ink)` puts the strokes in the order they were written.
    A stroke starts at `t0` plus its first sample's `t`, and the file's
    order breaks ties. A stroke with no timing counts as written right
    after the stroke before it in the file, its samples spread over
    300 ms. Each sample gets a replay time: gaps between strokes and
    inside one are cut to `REPLAY_PAUSE` (400 ms), and the whole replay
    is scaled down to `REPLAY_MAX` (15 s) when longer.
  - `inkAtTime(ink, timeline, t)` is the drawing at replay time `t`: the
    strokes begun, and the one being written cut short. A prefix of a
    stroke's delta-coded `pts` is its first samples, so that is a slice.
  - `ink/inkReplay.js` plays one replay at a time on animation frames,
    under the id of what it shows (a group, or a sheet's whole
    handwriting). `InkLayer.jsx` `useReplayOf(ids)` gives a layer or a
    card the frame of one of its own groups, so only they re-render; the
    layer draws that group as it stood, the card too.
    `InkReplayButton` starts and stops it. An edit to the group
    (`applyInk`) or leaving the page ends it.
- `ink/inkStore.js`: files by URL, and per-block **drafts** — the strokes as
  edited here, ahead of upload. A draft wins over the block's file until
  the upload replaces `ink_url` with the draft's; a remote `ink_url` change
  on a block with nothing unsaved drops the draft.
- `ink/InkLayer.jsx`: `InkLayer` (one per surface: a PDF page, a sheet in
  the notebook view, a sheet among the notes; it draws after the
  highlights and the text boxes) is the retained SVG plus a
  `desynchronized` canvas for the stroke in progress. `InkSelectionMenu`
  is a portalled `ContextMenu` (its controls sit outside the page's pointer
  listeners); `useSelectionMenuAnchor` places it, measuring its height, and
  places a text box's menu too. `InkTransformHandles` are the resize/rotate
  buttons; `InkTooltips` shows a button's title on hover or focus for the
  strip and the menus, since native titles are unreliable under Pencil
  hover. `InkCard` is the picture in the notes. For `markup/` the file
  exports `InkTooltips`, `inkColorName`, `ERASER_SIZES`, `ColorChoices` (a
  palette's swatches plus a custom colour, for a pen preset's row and a
  text box's colours) and `swallowClick` (eats the click a handled
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
    (Safari has none but delivers 120/240 Hz moves), hardware timestamps,
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
- `markup/` ([text_boxes.md](text_boxes.md) "Client"):
  - `MarkupToolbar.jsx` `MarkupToolbar` is the strip: the presets, the
    eraser, the lasso, the Text tool and their options rows, the hand,
    close and the history buttons, whose labels follow its `blockHistory`
    flag.
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
    eraserSize, lassoMode}, text, actions}`. The actions are the stroke,
    erase, select, action, move, jump and finger-scroll handlers (and the
    text boxes'),
    ref-backed through `useStableActions`, so their identity never changes.
    `armedClasses(tools)` gives a surface's container its armed-tool
    classes (`inkArmed`, `inkTouchDraw`, `textArmed`).
- `app/App.jsx` owns the tool state: `inkUi` (`open`, the armed `tool` — a
  preset id, `eraser`, `select`, `text` (the Text tool) or `null` for the
  hand — its `options` row,
  and `pen`, the last pen preset, which a stylus writes with when nothing
  is armed) plus the prefs (`inkTools`, the preset list validated by
  `ink/ink.js` `normalizeTools`; the eraser's mode and size; the lasso mode),
  the group the next stroke joins (`inkActiveRef`), the lasso
  selection (`inkSelection`) and the **stroke history** (`inkHistRef`:
  entries of `{changes: [{id, page, before, after}], label}`, one per action; a group whose
  block is gone is re-inserted when an entry brings strokes back). Every
  edit funnels through `applyInk`, which updates the drafts, records the
  entry and schedules `flushInk` (700 ms after the
  last one, and on `pagehide` / `visibilitychange` / leaving the page).
  The flush uploads the draft (`POST /api/upload-ink`, the `serializeInk`
  bytes) and PATCHes the block through `PUT /api/blocks/{id}` with
  `inkProps` (the url, count and position) and `base_properties: {ink_url}`,
  the file the draft was edited from. That is a server-side writer, so the
  change fans out over the page socket and reaches this tree like a
  remote op. Only the group's block itself (first stroke) is inserted
  through the tree.
  - A draft remembers its base (`inkStore` drafts: `{ink, dirty, url,
    base}`). When the answer names another file than the upload, the
    server merged someone else's strokes: `markSaved` loads the merged
    file and replaces the draft with it, with strokes drawn meanwhile
    merged on top.
  - An empty group with no caption and no notes is deleted the same way.
    Its empty draft keeps masking the saved strokes until the tree sees
    the deletion (the HTTP response can land before the socket op).
    `inkStore.markDeleted` marks it clean only after a successful delete,
    so a failure retries. An empty group with a caption or notes is saved
    as an empty drawing instead.
  - A failed flush (the block's insert may still be queued) retries after
    two seconds.
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
  writers, one group"). Each client keeps a fresh group after
  *New group*.
  A focused note editor keeps the block history even with the strip open,
  and other text inputs keep their own undo; an empty ink history never
  falls through to block undo. Undo and redo report the action and PDF page
  in the status pill (“Undone: ink width change (page 1).”); the labels
  distinguish drawing, erasure, partial erasure, move, colour, width,
  duplicate and delete.

## Server

- `gamma/ink.py`: the pydantic schema and limits, the codec, `read_upload`
  (the parsed file behind a block's `ink_url`, None when unreadable — the
  exporters' one loader),
  `stroke_polyline` (variable-width polylines every renderer draws from),
  `bounding_box` / `pdf_position` (the shape is `gamma/highlights.py`'s),
  `to_svg`, `pdf_path_ops` (content-stream operators for the notes-as-PDF
  writer), `ink_buckets` and `from_pdf_ink`
  for the `/Ink` interchange, `dumps` (canonical bytes: sorted keys, so the
  same strokes dedup to one upload).
- `POST /api/upload-ink` (`routers/ink.py`): the file as the JSON body,
  validated, stored as it came as `<hash of the bytes>.ink` with the usual
  quota check → `{url, size, strokes, bbox, pdf_position, already_existed}`.
  `POST /api/upload-file`, the route a mirror pushes files by, stores an
  `.ink` only when it is a valid drawing. Editors and
  edit shares (`require_ws_writer`). `.ink` is in `storage.FILE_MEDIA_TYPES`
  (`application/json`), so `GET /api/uploads/<hash>.ink` is the ordinary
  upload route with its share scoping and cache headers.
- Interchange ([import_export.md](import_export.md),
  [pdf_typesetting.md](pdf_typesetting.md)): the annotated PDF
  writes one `/Ink` per look bucket (colour × tool × size × opacity) with
  `/InkList` in user space, the mean drawn width as `/BS /W`, the note on
  the first, an `/NM` for Zotero, and a private `/GammaInk` key carrying the
  bucket's strokes so a Gamma re-import keeps pressure and time; the
  embedded-annotation importer reads `/Ink` (Gamma's or anyone's) into ink
  blocks and strips them like the other types; the Markdown export writes
  `![Handwriting (p.N)](assets/<hash>.svg)` with the SVG generated into the
  zip; the notes-as-PDF document draws the strokes as vectors under a
  "handwriting, p. N" line. The agent's `read_block` outline labels an ink
  block "handwriting on p. N, K strokes" before its caption.
- `gamma/ink_view.py`: the picture the chat sees of a group (`view_ink`,
  and an attached handwriting block), drawn where it was written. A sheet
  goes through `notebook_pdf`, a PDF page through
  `pdf_export.page_with_ink` (that page alone with the groups as `/Ink`),
  and pdfium rasterizes either ([ai_tools.md](ai_tools.md) "view_ink").

## Not built yet

Shape tools, reordering presets
by drag, syncing the preset row across devices (it is per browser),
ballpoint / fountain / dashed pen styles, Xournal++ `.xopp` import,
live co-drawing over presence, audio recording to go with the replay, and a
replay of erasures and edits (it shows the drawing as it is, in the order
it was written). Obsidian vault export writes an ink block's
caption only. The Notability comparison in the research note lists what a
closer pen experience still needs (draw-and-hold straightening, an eraser
that returns to the last tool, the highlighter behind the ink, clipboard
operations). The broader interaction survey is
[handwriting-interactions.md](../research/handwriting-interactions.md).
