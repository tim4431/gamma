# Text boxes

Typed text placed on a PDF page or a sheet of paper, like Acrobat's Add
Text Box or a Notability text box. A text box is a block: its content is
its text, Markdown like any note. So search, the text merge, the exports
and the AI tools read it as a note. One property, `text_box`, places it.

Code: `frontend/src/markup/` (`textBox.js`, `useTextBoxes.js`,
`TextBoxLayer.jsx`, `markup.css`, and the parts shared with handwriting:
`PageTools.jsx`, `MarkupLayers.jsx`, `MarkupToolbar.jsx`), `gamma/text_box.py`
(the server's rules and the PDF layout), the `/FreeText` writer in
`gamma/pdf_export.py`, the reader in `gamma/routers/imports.py`, and
`gamma/notebook.py` `notebook_pdf`. Tests are listed [below](#tests).

## What the user sees

The [user guide](../user_guide.md#type-on-the-page) walks through it.
These are the rules the code keeps.

- **The Text tool** sits on the markup strip after the lasso
  ([handwriting.md](handwriting.md)); `T` arms it. Armed, the page takes
  every press, a finger's too, before a link or the text layer, and the ink
  layer only draws.
- **Placing.** A tap makes an auto-width box with its first line centred
  on the pointer. A drag over 6 CSS px makes a fixed-width box as wide as
  the drag.
- **Typing.** In the box, Enter is a new line and only the formatting keys
  work ([hotkeys.md](hotkeys.md#widget-keys-the-markup-strip-and-a-text-box)).
- **An empty box** with no notes under it goes when its editor closes. A
  box made and left empty leaves no undo entry and keeps the redo steps.
- **Selecting.** With the hand armed or the strip closed, a click selects a
  box, over a link too, and a second click opens its editor. At most one
  box is selected or edited in the app, and never beside a lasso selection.
- **Moving.** A drag over 6 CSS px moves a box. Without the Text tool, a
  finger drags only the selected box, the only one with `touch-action: none`.
- A second contact makes any press a pinch, which moves nothing.
- **Undo.** While the Text tool is armed or a box is selected or edited,
  Ctrl+Z and the strip's buttons step the block history, not the strokes
  ([handwriting.md](handwriting.md)). Changes to one box less than a second
  apart are one step.
- **In the notes** a box is a row whose "T" marker shows it on its page. A
  box's editor and its row's are never open at once.

## Model

No schema change, no migration, no new endpoint: a text box is a block
with `text_box`, plus `pdf_page` (the 1-based page) on a PDF page. A box
imported from the PDF's own `/FreeText` or `/Text` also carries
`imported_annot` and `annot_stripped`, as highlights do.

| `text_box` key | meaning | default |
|---|---|---|
| `x`, `y` | the outer top-left corner, padding included | 0 |
| `w` | the outer width, at least 24 (`MIN_WIDTH`) | 200 |
| `h` | the outer height as a client last measured it | one line, `size × LINE + 2 × PAD` |
| `auto` | the width follows the text up to the surface's edge; false is fixed | true |
| `size` | the font size in pt, 6 to 96; `TEXT_SIZES` offers 9 to 36 | 12 |
| `color` | the text colour, `#rrggbb` | `#1f1f1f` |
| `bg` | the fill, `#rrggbb`; `TEXT_BACKGROUNDS` offers white, note yellow, light blue | `null` |

`PAD` is 4 pt and `LINE` 1.25 times the size. Every value is at most
`MAX_COORD`, 14 400 pt, the largest page side a PDF allows (ISO 32000-1,
Annex C). On a PDF page the frame is the one ink files use
([handwriting.md](handwriting.md#the-stroke-file-gamma-ink-v1)); on a sheet
it is points from the sheet's top-left.

**Normalization.** Every reader goes through `normalize_text_box`
(`gamma/text_box.py`) or `normalizeTextBox` (`markup/textBox.js`). A bad
key takes its default, numbers are clamped and rounded to hundredths,
colours lowercased, unknown keys dropped. A value that is not an object is
no text box. The two sides must agree on these details:

- Rounding goes halves up, as `Math.round` does; Python's `round` goes to
  even. Ties are common: size 10.5 gives a default `h` of 21.125, so 21.13.
- A non-finite number takes the default, not the bound. So does an integer
  too long for a double, which `JSON.parse` reads as Infinity.
- Only a real boolean is an `auto`: "false", 0 and null read as true.
- A colour matches whole (Python's `fullmatch`: `$` also matches before a
  trailing newline). A whole number stays an integer (23, not 23.0).

`tests/shared/textbox.json` pins both. `test_shared_fixtures.py` compares
JSON text, because Python's `True == 1` and `23 == 23.0` would hide a bad
value.

**Placement: the nearest sheet wins.** A box on a PDF page is made at the
page's top level, like an ink group. A box on a sheet is made as the
sheet's last child. A box stores no `pdf_position`, which every move would
rewrite, and no `highlight_id`, so nothing takes it for an area highlight.
Every reader places a box by one rule:

- under a sheet, it is on the nearest sheet above it, whatever `pdf_page`
  it carries ([notebooks.md](notebooks.md));
- otherwise it is on its `pdf_page` when that is a positive integer;
- otherwise it is on no surface and stays a note.

The client's `textBoxesBySurface(tree)` keeps the tree's own objects, so an
unchanged box keeps its identity. The server's `text_box.box_page(props,
on_sheet)` takes `on_sheet` from the writer's own walk of the tree.

**Measurement.** `w` (of an auto box) and `h` are what the box measured at
on this screen. The client writes them only right after a local edit of the
box or its text, folded into that edit's undo step. A passive render never
writes them: a page opening, another client's edit, the agent's
`edit_block`, a row edited on the iPad. Two clients whose fonts measure a
hair apart would otherwise write the size back and forth.

So a stored `h` can be stale, and nothing on screen reads it. A box is
drawn as tall as its text, and the PDF writers grow a box to its text.
`refitBox` writes nothing within half a point. The screen's font stack
(`Helvetica, Arial, "Liberation Sans", "Noto Sans", sans-serif`) is
metric-compatible with PDF Helvetica, so a box wraps alike in both.

**Merge.** Every change sends the whole `text_box`, since a keystroke
stores the measured size. Taken as one value, someone typing would undo
another's move. So a `set` of `text_box` whose `base_props` names the box
it was changed from merges key by key.

- `merge_text_box(stored, mine, base)` (`gamma/text_box.py`, called in
  `ops.py` beside the ink merge) is the stored box with the keys the writer
  changed from its base. Keys compare normalized, so a default spelled out
  is no change. The later writer wins a key both changed.
- A writer that names no base box replaces it: an import, the agent's
  tools, a `PUT /blocks/{id}` without `base_properties`.
- `mergeTextBox` (`markup/textBox.js`) is the twin;
  `tests/shared/textboxmerge.json` pins both.
- The browser names the base on every change ([collab.md](collab.md)
  "same-box merge"). So do the mirror and the iPad's replica, which also
  judge a lost push key by key ([mirror.md](mirror.md)).

## Client

`markup/` holds what a page surface carries, and the tool strip. A surface
is a PDF page number or a sheet id, the key ink uses. The parts shared with
ink are in [handwriting.md](handwriting.md#client).

- `PageTools.jsx`: `PageToolsContext` carries `text: {armed, style}` beside
  the ink tools, and the text-box actions among its `actions`. `ink.tool`
  and `ink.penTool` are null while the Text tool is armed.
- `MarkupLayers.jsx`: the text-box layer comes first and the ink layer over
  it, both at z-index 6, above a PDF page's highlights (2), note badges (4)
  and link boxes (5). A box takes its own clicks, and ink draws over
  everything. On a read-only page a tap on a box is not a tap on a stroke
  (`ABOVE_PASSIVE_INK`).
- `MarkupToolbar.jsx`: a press on the Text tool, its options row or Undo /
  Redo keeps an open editor focused.
- `textBox.js`: the pure rules and the geometry (`placeBox`, `refitBox`,
  `moveBox`, `resizeBox`). Its relative imports carry `.js`, so node loads it.
- `TextBoxLayer.jsx`: one surface's boxes.
  - The layer is a size container whose `--tb-pt` is one point of the
    surface (`calc(100cqw / width)`), so a zoom needs no re-render.
  - With the Text tool armed, a capture-phase `pointerdown` listener on the
    page wrapper claims a press, as `InkLayer` claims a stroke.
    `swallowClick` eats the click that ends it.
  - A press on a box (`begin`) takes the pointer at once on the band and the
    handle. On the text it waits for a move, so Ctrl+click reaches a link.
  - Another contact marks the press `dead`, a pinch, until its pointer lifts.
  - After an edit here, a box's layout effect reports its rendered size
    (`wantsMeasure`, `onBoxMeasured`). The editor opens with `pinScroll`,
    so opening it scrolls nothing.
  - `TextBoxMenu` uses the lasso menu's classes and placement
    (`useSelectionMenuAnchor`). `TextStyleChoices`, shared with the strip,
    takes its colours from `ColorChoices`, the pen presets' swatches.
- `useTextBoxes.js`: App's side. It owns the selection `{id, editing, at}`
  and the flash, and makes every edit through `setBlocks`, so the op diff
  sends it and the history records it.
  - A box is marked for measuring inside the tree update, only when the
    update changed the tree. A no-op edit then leaves no mark for a later
    remote edit to measure by.
  - A box made here and not typed in yet (`freshRef`) takes its changes
    through `foldBlocks`, into its making.
  - While a box is edited, a capture-phase listener notes which row a press
    lands on. The box's editor loses focus before that row's editor opens,
    so an empty box whose own row was pressed is kept.
- `markup.css`: the layer, frame, band, handle and read mode. The fixed
  code, link and mark colours sit in a `ds-allow` block
  ([ui-design.md](ui-design.md#ratchets)). The dark-page filter is ink's
  rule in `app.css`, extended to `.textBoxBody`.
- `app/App.jsx`:
  - `inkUi.tool === "text"` arms the tool (`textArmed`); `inkPenTool` is
    null meanwhile, so a stylus places boxes too.
  - `boxUndo` (the Text tool armed, or a box selected or edited) sends the
    strip's buttons to `undoBlocks` and leaves Ctrl+Z to the block undo.
    Escape goes to a selected or edited box.
  - `foldBlocks(fn)` is `setBlocks` for a write that belongs to the edit
    before it, marked `"fold"`: the block history records no entry. A fold
    back to the newest entry's tree takes that entry back.
  - `showOnPage(id)` shows an ink group or a box where it is drawn and
    flashes it; `showInNotes(id)` is the reverse.
- `editor/BlockTree.jsx`: a box's row draws its "T" marker in the branch
  that draws ink's pen marker. `onSheet` goes down the recursion, so a box
  under a sheet shows no `p.N`. App's `onChangeText` calls
  `textBoxes.touch`, so typing in the row refits the box.
- `editor/blockHistory.js`: `describeTransition` names a box's creation,
  deletion and text edit, and `textBoxChange` a move, style change or
  resize. `observeTree` merges changes of one box's `text_box` alone
  (`boxChange`) within `EDIT_MERGE_MS`. `rebaseHistory` carries another
  client's box onto each entry key by key.
- `collaboration/collabSession.js` and `shared/model/blockOps.js` are the
  browser's half of the merge ([collab.md](collab.md) "same-box merge").
  `ink/InkLayer.jsx`'s `NOT_INK` keeps a press on a box's handle, band or
  open editor from starting ink.

**Screen and PDF layout are one design**: the read mode's styles and
`gamma/text_box.py`'s layout. Both have no paragraph margins and a line
per source line (`remark-breaks`), with headings at 1.45, 1.25 and 1.1 em
on the box's leading. Lists are 1.5 em per level with the marker hanging in
the indent, a to-do's box too. Code is Courier; the code tint, link blue
and mark yellow are the export's. A change to one side needs the other.

## Server and interchange

- `gamma/text_box.py`: the rules above, and the box in a PDF, which both
  PDF writers draw with. `_paragraphs` reads the Markdown through
  `pdf_document.chunks`, the shared engine
  ([import_export.md](import_export.md#the-shared-typesetting-engine)).
  - `plain_text` is the text without marks, math as TeX. `measure` is the
    size a box's text needs, for a box made on the server. `pdf_ops` draws
    a box from its top-left in a y-down frame and returns the drawn size.
  - `plain_text` and `pdf_ops` take the notes PDF's `resolve_ref`
    (`_block_ref_resolver`), so a `[[ref]]` reads as its chip does.
  - `markdown_of` escapes plain text another app wrote (`escape_markdown`),
    so `plain_text` gives the text back.
- **The layout** (`_layout`):
  - The baseline is 0.9715 em below each line's top, where CSS puts it at
    line height 1.25 for Arial or Liberation Sans (0.905 em ascent, 0.212 em
    descent). The screen stack resolves Helvetica to those on Windows and
    Linux.
  - A fixed box wraps at `w − 2 × PAD`. An auto box wraps only at the
    page's edge and widens to its widest line, so a small metric difference
    widens the box instead of adding a line. Any box grows downward.
  - Display math is 1.21 em (KaTeX's size), scaled down to fit. A table is a
    line per row, an image its alt text. Blank lines take no height.
  - `pdf_typeset.draw_spans` passes the box's colour to `GlyphFonts.draw`
    and `vector_text.header`, so math and CJK take it too.

**The annotated PDF.** `routers/export.py` `_collect_text_boxes` keeps each
box with text whose `box_page` is set and that no sheet holds.
`pdf_export.annotate_pdf` writes each as a `/FreeText` before the ink and
the highlights, so under them, as on the page.

- **An embedded original is replaced.** Highlights and ink still embedded
  in the file are skipped, since the original stands for them. A box
  imported from the file is written anyway, and its `imported_annot` goes
  to `annotate_pdf`'s `replaced`.
- `_drop_replaced` removes each shown, non-reply `/FreeText` or `/Text`
  (`TEXT_BOX_TYPES`) with a replaced `annotation_key`, with its popup and
  thread. The reason: the viewer hides the file's own annotations by
  default, so the page shows Gamma's box, and an edit made here must reach
  the export.
- `highlight_note_text`, a highlight's or ink group's popup, follows sheet
  ancestry. It leaves out a box on a PDF page with the notes under it,
  since that box is its own `/FreeText`. A box under a sheet stays a line.
- **The switches.** A box is in the annotation layer and it is the user's
  writing, so either switch writes it. Only both off, the stored file,
  leaves the boxes out; ink and highlights need `highlights=1`.

**The `/FreeText` keys, and why:**

- `/IT /FreeText`, the plain text-box intent. Not `FreeTextTypeWriter`: ISO
  and Acrobat spell it differently, and Acrobat's typewriter has no fill.
- `/Rect`: the drawn box, grown to its text, through the view-box- and
  rotation-aware `viewer_point_to_pdf`. The view box (`page_frame`) is what
  pdf.js, pdfium and MuPDF show: the crop box clipped to the media box.
- `/Contents`: `plain_text`. `/DA`: `/Helv <size> Tf r g b rg`, which
  pdf.js, MuPDF and Acrobat read to rebuild the look. No AcroForm `/DR`:
  adding a form to a PDF without one has side effects.
- `/C`: the fill, only when `bg` is set; Acrobat, MuPDF and pypdf read it as
  the background. `/IC` is not FreeText's.
- `/BS << /W 0 >>`: no border. `/F 4`: print. `/NM` from the block id, `/T`
  the author, `/M` the block's `updated_at`.
- `/Rotate` on a turned page: Adobe's key, read by pdf.js and MuPDF, keeps
  a rebuilt look upright. NoRotate is not set: the appearance's matrix
  already keeps the text upright, and NoRotate support varies.
- `/AP /N`: a form typeset by `text_box.pdf_ops`, `/BBox` `[0 0 w h]` in
  display orientation. Its `/Matrix` turns it back against `/Rotate` (90:
  `[0 1 −1 0 h 0]`, 180: `[−1 0 0 −1 w h]`, 270: `[0 −1 1 0 0 w]`).
- `/GammaTextBox`: `{"v":1,"md":…,"box":…,"text":…}`, for a lossless
  re-import. `text` is the `/Contents` written, so the import can tell
  another viewer's edit from a reference label.
- Not written: `/RC` and `/DS`, which could disagree with the appearance,
  and `/Q`, 0 by default. Nor `/RD`, since a rebuilt look would fill only
  inside it, or `/CL` and `/LE`, which are for callouts.

**Import** (`_text_box_from_annotation`; the endpoint and its rules for
replies and hidden annotations are in
[import_export.md](import_export.md#importing-annotations-embedded-in-a-pdf)):

- A `/FreeText` or `/Text` becomes a text box. Its place comes from `/Rect`
  through `pdf_point_to_viewer`, the exact inverse of the export's mapping,
  and `_on_page` keeps it inside the page.
- With `/GammaTextBox`, the Markdown, size and style come from it and `x`,
  `y` from `/Rect`, so a move in another viewer holds. When `/Contents`
  (`\r` read as `\n`) differs from the `text` written, `/Contents` wins.
- Other text is plain text: `markdown_of` escapes it, so "$5 and $10" reads
  as the other app showed it.
- A foreign `/FreeText` is a fixed box over its text area, the `/Rect` less
  the `/RD` margins. Its size and colour come from `/DA`, its fill from `/C`,
  its text from `/Contents`, else `/RC` reduced by `_rc_text`.
- A `/Text` sticky note is a note-yellow auto box at its icon's top-left,
  sized by `measure`.
- The key (`annotation_key`: page, subtype, the rounded corners of the
  `/Rect`) is the one a highlight block made from the same annotation
  carries. Such a block counts as the box, so nothing comes twice.
- Only text boxes use the rotation-aware inverse. The highlight, area and
  ink import does a plain media-box flip, which agrees with the export
  wherever the view box is the media box.

**Notebook PDF** (`notebook.notebook_pdf`, [notebooks.md](notebooks.md)):
each sheet's boxes with text are page content, selectable text, after the
paper and before the ink.

**The other readers** treat a box as a note and never as an area
highlight, placed by the nearest-sheet rule.

- The text exports ([import_export.md](import_export.md#text-boxes-in-the-exports))
  keep a box with `highlights=0` and drop it with `notes=0`. For the
  annotated PDF `notes=0`, the default, means "don't paint the notes", not
  "leave out my writing".
- The agent's `read_block` and the chat context label a box by where it is
  (`ai_context.text_box_label`; [ai_tools.md](ai_tools.md),
  [ai_context.md](ai_context.md)). A walk that starts at a block asks
  `ai_context.under_sheet` once.
- `ai_context.area_highlight` never answers for a box. `move_block` refuses
  to move a box to another page unless its sheet moves too
  (`ai_tools._loose_text_box`). A search hit on a box is a note
  (`routers/blocks.py` `_block_kind`).

## Tests

- `frontend/tests/textBox.test.mjs`: the shared cases, `textBoxesBySurface`
  (nested sheets, a stale `pdf_page`, identity), `textStyle`, the geometry.
  `blockHistory.test.mjs`: the labels, folds, runs and the key-by-key
  rebase. Also `blockOps.test.mjs`, `collabSession.test.mjs`
  ([collab.md](collab.md#testing)) and `replica.test.mjs`.
- `backend/tests/test_text_box_merge.py`: the merge through `/ops` and
  `PUT /blocks/{id}`, a writer with no base box, removal, the mirror's diff.
- `backend/tests/test_text_box.py`: the `/FreeText` keys and their
  geometry at every rotation, with a pypdfium2 pixel check, the layout and
  the round trip to 0.01 pt. Also edits in another viewer, foreign boxes,
  sticky notes and threads, replaced originals, the switches and the
  notebook PDF.
- `backend/tests/test_shared_fixtures.py`, and label checks in the export,
  Obsidian, Zotero, notes PDF, block and AI tool tests.
- Browser: `textBoxes.mjs`, group `textbox`
  ([debugging.md](debugging.md#browser-end-to-end-suite)). `select.mjs`
  runs it for changes under `frontend/src/markup/`, `ink/`, `notebook/`,
  `editor/`, `pdf/` and `backend/gamma/text_box.py`.

## Not built yet

- The iPad app neither draws nor makes text boxes; a box is a note row
  there ([ipad.md](ipad.md)).
- Nothing drops `pdf_page` when a box moves under a sheet in the notes, or
  sets it when a box moves back to a PDF page's top level. A box moved to
  another page keeps its `pdf_page` and shows on that page of the new paper.
- Undo: after a selected box is deleted with the hand armed and the strip
  open, Ctrl+Z steps the strokes. A run of differing changes (a move, then
  a restyle) is one step, named for the first.
- A press on an empty new box's own row keeps the box even when it opens no
  editor (its marker, a drag of the row).
- On a read-only page a stroke shows no pointer cursor, since the strokes
  take no pointer there.
- Tables, images and to-dos look different on screen and in the PDF. On
  macOS the screen's Helvetica sits about 0.08 em higher than the PDF's.
- The menu does not re-place itself when the strip opens, closes or grows.
- The annotated PDF is checked with pdfium and pdf.js, not in Acrobat or
  macOS Preview, which may draw its own look from `/Contents`, `/DA`, `/C`.
- A box's child notes, a sticky note's replies among them, are not in the
  annotated PDF: a FreeText's `/Contents` must be the text it shows.
- With `notes=1`, a painted note can overlap a box: free space comes from
  page objects, and annotations are not page objects.
- With *Keep originals* (not the default), a box imported from the file and
  deleted here leaves its embedded original in the export: nothing records
  a deleted block. The default strips the original at import, so it goes.
- Import drops a callout's line and arrow. A foreign text's blank lines and
  leading spaces take no room in a box and leave the next `/Contents`.
- A reply's text is kept as written, as a highlight's comment is, so
  Markdown-looking text there renders as Markdown.
- A `/Text` or `/FreeText` stored as a highlight block stays one, which the
  AI and the Logseq export read as an area highlight; there is no migration.
- The Zotero note labels a box by its page only as the note's top block.
  The Obsidian export does not say which sheet a box was on.
- Other fonts, alignment, borders and rotated boxes: the model has none.
