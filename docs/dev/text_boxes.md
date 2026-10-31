# Text boxes

Typed text placed on a PDF page or a sheet of paper, like Acrobat's Add
Text Box or a Notability text box. A text box is a block: its content is
its text, Markdown like any note. So search, the text merge, the exports
and the AI tools read it as a note. One property, `text_box`, places it.

Code: `frontend/src/markup/` (`textBox.js`, `useTextBoxes.js`,
`TextBoxLayer.jsx`, `markup.css`, the parts shared with handwriting:
`PageTools.jsx`, `MarkupLayers.jsx`, `MarkupToolbar.jsx`, and the part
shared with pictures: `placed.js`), `gamma/text_box.py`
(the server's rules and the PDF layout), the `/FreeText` writer in
`gamma/pdf_export.py`, the reader in `gamma/routers/imports.py`, the boxes
the Notability import makes in `gamma/notability_map.py`, and
`gamma/notebook.py` `notebook_pdf`. Tests are listed [below](#tests).

A picture ([pictures.md](pictures.md)) is the other object placed on a
surface. It is placed by a box's rule, and it shares the box's
selection, merge table and tree edits. The sections below name it where
it does what a box does.

## What the user sees

The [user guide](../user_guide/user_guide.md#draw-and-type-on-the-page) walks through it.
These are the rules the code keeps.

- **The Text tool** sits on the markup strip after the lasso
  ([handwriting.md](handwriting.md)); `T` arms it. Armed, the page takes
  every press, a finger's too, before a link or the text layer, and the ink
  layer only draws.
  - A press on a selected picture, its handles or its crop stays the
    picture's. A picture placed while the tool is armed lands selected, so
    it can be moved before a box is typed over it.
- **Placing.** A tap makes an auto-width box with its first line centred
  on the pointer. A drag over 6 CSS px makes a fixed-width box as wide as
  the drag.
- **Typing.** In the box, Enter is a new line and only the formatting keys
  work ([hotkeys.md](hotkeys.md#widget-keys-the-markup-strip-and-a-text-box)).
- **An empty box** with no notes under it goes when its editor closes. A
  box made and left empty leaves no undo entry and keeps the redo steps.
- **Selecting.** With the hand armed or the strip closed, a click selects a
  box, over a link too, and a second click opens its editor. The app has
  one selection: one box selected or edited, one picture selected, or the
  lasso's strokes. Selecting one lets go of the others.
- **Moving.** A drag over 6 CSS px moves a box. Without the Text tool, a
  finger drags only the selected box, the only one with `touch-action: none`.
- A second contact makes any press a pinch, which moves nothing.
- **Undo.** While the Text tool is armed, a box is selected or edited, or
  a picture is selected, Ctrl+Z and the strip's buttons step the block
  history, not the strokes ([handwriting.md](handwriting.md)). Changes to
  one box less than a second apart are one step, and so are a picture's.
- **In the notes** a box is a row whose "T" marker shows it on its page. A
  box's editor and its row's are never open at once.

## Model

No schema change, no migration, no new endpoint: a text box is a block
with `text_box`, plus `pdf_page` (the 1-based page) on a PDF page. A box
and a picture are the blocks with a `pdf_page`: a highlight, a link region
and an ink group are on the page their `pdf_position` names
([api.md](api.md) "The highlight shape"). A box
imported from the PDF's own `/FreeText` or `/Text` also carries
`imported_annot` and `annot_stripped`, as highlights do.

| `text_box` key | meaning | default |
|---|---|---|
| `x`, `y` | the outer top-left corner, padding included | 0 |
| `w` | the outer width, at least 24 (`MIN_WIDTH`) | 200 |
| `h` | the outer height as a client last measured it | one line, `size × LINE + 2 × PAD`, a Zotero box's too |
| `auto` | the width follows the text up to the surface's edge; false is fixed | true |
| `size` | the font size in pt, 6 to 96; `TEXT_SIZES` offers 9 to 36 | 12 |
| `color` | the text colour, `#rrggbb` | `#1f1f1f` |
| `bg` | the fill, `#rrggbb`; `TEXT_BACKGROUNDS` offers white, note yellow, light blue | `null` |
| `zotero` | `true`: a [Zotero box](#zotero-boxes), with no padding, so `x`, `y`, `w`, `h` are its text area | absent |

`PAD` is 4 pt and `LINE` 1.25 times the size. Every value is at most
`MAX_COORD`, 14 400 pt, the largest page side a PDF allows (ISO 32000-1,
Annex C). On a PDF page the frame is the one ink files use
([handwriting.md](handwriting.md#the-stroke-file-gamma-ink-v2)); on a sheet
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
  Only `true` is a `zotero`; any other value leaves the key out, so a
  Gamma box is stored as it was before the key existed.
- A colour matches whole (Python's `fullmatch`: `$` also matches before a
  trailing newline). A whole number stays an integer (23, not 23.0).

`tests/shared/textbox.json` pins both, and `tests/shared/zoterotext.json`
the Zotero box's rules. `test_shared_fixtures.py` compares
JSON text, because Python's `True == 1` and `23 == 23.0` would hide a bad
value.

**Placement: the nearest sheet wins.** A box on a PDF page is made at the
page's top level, like an ink group. A box on a sheet is made as the
sheet's last child. A box stores no `pdf_position`, which every move would
rewrite and which would make it a highlight; the block table's `kind`
and `highlights.is_highlight` test for a box first all the same, so
nothing takes it for an area highlight.
Every reader places a box by one rule:

- under a sheet, it is on the nearest sheet above it, whatever `pdf_page`
  it carries ([notebooks.md](notebooks.md));
- otherwise it is on its `pdf_page` when that is a positive integer;
- otherwise it is on no surface and stays a note.

The client's `textBoxesBySurface(tree)` keeps the tree's own objects, so an
unchanged box keeps its identity. The server's `text_box.box_page(props,
on_sheet)` takes `on_sheet` from the writer's own walk of the tree. A
picture is placed by the same rule (`picture.js` `picturesBySurface`,
`picture.picture_page`), and a new one goes where a new box goes: one
function, `markup/placed.js` `addPlaced`, makes both.

**Measurement.** `w` (of an auto box) and `h` are what the box measured at
on this screen. The client writes them only right after a local edit of the
box or its text, folded into that edit's undo step. A passive render never
writes them: a page opening, another client's edit, the agent's
`edit_block`, a row edited on the iPad. Two clients whose fonts measure a
hair apart would otherwise write the size back and forth.

So a stored `h` can be stale, and nothing on screen reads it. A box is
drawn as tall as its text, and the PDF writers grow a box to its text.
`refitBox` writes nothing within half a point. A Gamma box's screen font
stack (`Helvetica, Arial, "Liberation Sans", "Noto Sans", sans-serif`) is
metric-compatible with PDF Helvetica, so it wraps alike in both. A Zotero
box draws in Zotero's reader's stack instead, and an auto one stores its
measured width plus `ZOTERO_SLACK` ([Zotero boxes](#zotero-boxes)).

**Merge.** Every change sends the whole `text_box`, since a keystroke
stores the measured size. Taken as one value, someone typing would undo
another's move. So a `set` of `text_box` whose `base_props` names the box
it was changed from merges key by key.

- `merge_text_box(stored, mine, base)` (`gamma/text_box.py`, called in
  `ops.py` beside the ink merge, through `PLACED_KEYS`) is the stored box
  with the keys the writer changed from its base. Keys compare normalized,
  so a default spelled out is no change. The later writer wins a key both
  changed.
- A writer that names no base box replaces it: an import, the agent's
  tools, a `PUT /blocks/{id}` without `base_properties`.
- The `zotero` key compares against the base's own (`was.get(k)`), so a
  writer whose base and box both lack it, such as an older client whose
  normalizer drops it, keeps the stored key, and one that adds it sets
  it. No merge removes it; a writer that names no base box replaces the
  box, key and all, and a Zotero link adds the key back
  ([zotero_sync.md](zotero_sync.md#text-boxes)).
- `mergeTextBox` (`markup/textBox.js`) is the twin;
  `tests/shared/textboxmerge.json` pins both, and the `merge` cases of
  `tests/shared/zoterotext.json` the `zotero` key.
- The browser names the base on every change ([collab.md](collab.md)
  "same-box merge"). So does the mirror, which
  also judges a lost push key by key ([mirror.md](mirror.md)).
- **The table it shares with pictures.** `ops.PLACED_KEYS` on the server
  and `blockOps.PLACED` on the client map each key that places an object,
  `text_box` and `picture`, to its normalizer and its merge. Every reader
  of the merge goes through the table: `ops.py`, the mirror's diff
  (`sync_tree._set_op`) and resend (`sync_engine._placed_again`),
  `diffTrees`, the live session (`ahead`, `storedPlaced`) and the undo
  history (`boxChange`, `rebaseHistory`). A picture's merge differs in one
  way: its frame merges as its size and its centre, not `x` and `y` key by
  key ([pictures.md](pictures.md#model) "Merge").

## Client

`markup/` holds what a page surface carries, and the tool strip. A surface
is a PDF page number or a sheet id, the key ink uses. The parts shared with
ink are in [handwriting.md](handwriting.md#client).

- `PageTools.jsx`: `PageToolsContext` carries `text: {armed, style, zotero}`
  beside the ink tools, and the text-box actions among its `actions`.
  `ink.tool` and `ink.penTool` are null while the Text tool is armed.
  `text.zotero` is App's `zoteroLinked`: the workspace has a Zotero link
  that is not detached (`syncLinks.js` `zoteroPdfSource`), outside a share
  link's view.
- `MarkupLayers.jsx`: the picture layer comes first, then the text-box
  layer and the ink layer over them, all at z-index 6, above a PDF page's
  highlights (2), note badges (4) and link boxes (5). A box takes its own
  clicks, and ink draws over everything. On a read-only page a tap on a
  box or a picture is not a tap on a stroke (`ABOVE_PASSIVE_INK`).
- `MarkupToolbar.jsx`: a press on the Text tool, its options row or Undo /
  Redo keeps an open editor focused.
- `textBox.js`: the pure rules and the geometry (`placeBox`, `refitBox`,
  `moveBox`, `resizeBox`, `padOf`), and `shownText`, the Markdown a Zotero
  box's read mode renders. Its relative imports carry `.js`, so node loads
  it.
- `placed.js` (pure): the two tree edits a box and a picture share.
  - `withPlaced(tree, id, key, fn)` replaces a block's `text_box` or
    `picture` with `fn` of its normalized value. It gives the same tree
    back when that changes nothing or the block holds no such value.
    `useTextBoxes`' `withBox` is `withPlaced` with `"text_box"`.
  - `addPlaced(tree, surface, id, properties)` adds a new block where the
    nearest-sheet rule puts it: on a PDF page at the top level with its
    `pdf_page`, on a sheet as the sheet's last child, nowhere when the
    sheet is gone. `onBoxCreate` makes a box through it.
- `TextBoxLayer.jsx`: one surface's boxes.
  - The layer is a size container whose `--tb-pt` is one point of the
    surface (`calc(100cqw / width)`), so a zoom needs no re-render.
  - With the Text tool armed, a capture-phase `pointerdown` listener on the
    page wrapper claims a press, as `InkLayer` claims a stroke.
    `swallowClick` eats the click that ends it.
  - The claim leaves alone a press on a box, the ruler, a selected picture
    and its controls (`.pagePicture.selected`, `.pictureHandle`,
    `.pictureCrop`) and a comment card.
  - A press on a box (`begin`) takes the pointer at once on the band and the
    handle. On the text it waits for a move, so Ctrl+click reaches a link.
  - Another contact marks the press `dead`, a pinch, until its pointer lifts.
  - With `text.zotero`, a box placed on a PDF page (`typeof surface ===
    "number"`) is placed as a Zotero box. A box with `zotero` takes the
    `zotero` class, and its read mode renders `shownText(content, box)`;
    the editor, the stored text and click-to-source read the content.
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
- `markup.css`: the layer, frame, band, handle and read mode, and a
  Zotero box's face and its inline links ([Zotero boxes](#zotero-boxes)). The fixed
  code, link and mark colours sit in a `ds-allow` block
  ([ui-design.md](ui-design.md#ratchets)). The dark-page filter is ink's
  rule in `app.css`, extended to `.textBoxBody`. In a fixed-width box a
  picture without a width of its own (`![alt](…)`, not `![alt|300](…)`)
  fills the box's text width, its frames too, so it zooms with the page
  as the text does; an older Notability import made a picture's box the
  picture's size ([import_export.md](import_export.md#notability-notes)).
  In an auto-width box, or sized in its Markdown, a picture keeps its own
  size.
- `ink/useInkStrip.js`: `inkUi.tool === "text"` arms the tool
  (`textArmed`); `inkPenTool` is null meanwhile, so a stylus places boxes
  too.
- `app/App.jsx`:
  - `boxUndo` (the Text tool armed, a box selected or edited, or a picture
    selected) sends the strip's buttons to `undoBlocks` and leaves Ctrl+Z
    to the block undo. Escape goes to a selected or edited box, or to the
    selected picture: `useStripKeys`' `hasBox` is true for either.
  - One selection: a box selected or opened lets go of the lasso's strokes
    and of the picture (`pictures.deselect()`). A picture selected lets go
    of the box and the strokes, and the lasso's selection lets go of both.
  - `foldBlocks(fn)` is `setBlocks` for a write that belongs to the edit
    before it, marked `"fold"`: the block history records no entry. A fold
    back to the newest entry's tree takes that entry back.
  - `showOnPage(id)` shows an ink group, a box or a picture where it is
    drawn and flashes it; `showInNotes(id)` is the reverse.
- `editor/BlockTree.jsx`: a box's row draws its "T" marker in the branch
  that draws ink's pen marker. `onSheet` goes down the recursion, so a box
  under a sheet shows no `p.N`. App's `onChangeText` calls
  `textBoxes.touch`, so typing in the row refits the box.
- `editor/blockHistory.js`: `describeTransition` names a box's creation,
  deletion and text edit, and `textBoxChange` a move, style change or
  resize. `observeTree` merges changes of one box's `text_box` alone
  (`boxChange`, which a picture's `picture` passes too) within
  `EDIT_MERGE_MS`. `rebaseHistory` carries another client's box onto each
  entry key by key, and a picture through its own merge (`blockOps.PLACED`).
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
A Zotero box's screen face is Zotero's, not the PDF's
([Zotero boxes](#zotero-boxes)).

## Server and interchange

- `gamma/text_box.py`: the rules above, and the box in a PDF, which both
  PDF writers draw with. `_paragraphs` reads the Markdown through
  `pdf_document.chunks`, the shared engine
  ([pdf_typesetting.md](pdf_typesetting.md#the-shared-typesetting-engine)).
  - `plain_text` is the text without marks, math as TeX. With `gaps` (a
    Zotero box's text) each blank source line between two others is an
    empty line, so `a\n\nb` and `a\n\n\nb` read back as written; blank
    lines at either end go. `measure` is the size a box's text needs, for
    a box made on the server. `pdf_ops` draws a box from its top-left in a
    y-down frame and returns the drawn size.
  - `zotero_frame(box, width, height)` is a box as a Zotero box with its
    text where it was: a Zotero box as it is; a Gamma box's text area,
    `x + PAD`, `y + PAD`, `w − 2 × PAD` (fixed) or `w` (auto: the padding
    becomes room for Zotero's face, which the client measures again at the
    box's next edit), and `(h − 2 × PAD) × ZOTERO_LINE / LINE`, the same
    lines at Zotero's line height. Kept on a surface of `width` ×
    `height` when both are given. The PDF export's `/Zotero:Rect` and the
    Zotero link's first send use it.
  - `plain_text` and `pdf_ops` take the notes PDF's `resolve_ref`
    (`_block_ref_resolver`), so a `[[ref]]` reads as its chip does.
  - `markdown_of` escapes plain text another app wrote (`escape_markdown`),
    so `plain_text` gives the text back.
- **The layout** (`_layout`):
  - The baseline is 0.9715 em below each line's top, where CSS puts it at
    line height 1.25 for Arial or Liberation Sans (0.905 em ascent, 0.212 em
    descent). The screen stack resolves Helvetica to those on Windows and
    Linux.
  - The padding is `pad_of(box)`: `PAD` for a Gamma box, 0 for a Zotero
    box. It sets the room, each row's x, the first line's top and the
    natural size.
  - A fixed box wraps at `w − 2 × pad_of(box)`. An auto box wraps only at
    the page's edge and widens to its widest line, so a small metric
    difference widens the box instead of adding a line. Any box grows
    downward.
  - Display math is 1.21 em (KaTeX's size), scaled down to fit and centred
    inside the padding. A table is a line per row, an image its alt text.
    Blank lines take no height in a Gamma box; in a Zotero box each blank
    line between two others takes a line, `size × LINE` (`_paragraphs`'
    `gap`), and those at either end are dropped.
  - Rows are `LINE` high in a Zotero box too: the server cannot know the
    screen's line height, and the PDF only has to fit its own appearance.
  - `pdf_typeset.draw_spans` passes the box's colour to `GlyphFonts.draw`
    and `vector_text.header`, so math and CJK take it too.

**The annotated PDF.** `routers/export.py` `_collect_text_boxes` keeps each
box with text whose `box_page` is set and that no sheet holds
(`_on_sheets`). It walks the page in document order
(`notebook.document_order`), the order the boxes stack in.
`_collect_pictures` beside it gathers the pictures by the same rules
([pictures.md](pictures.md#server-and-interchange)).
`pdf_export.annotate_pdf` writes the highlights and area notes first,
then the pictures (`/Stamp`), the boxes (`/FreeText`) and the ink.
Annotations paint in the order they are written, so the PDF stacks them
as the page does: a highlight under everything, a box over a picture, ink
over both.

- **An embedded original is replaced.** Highlights and ink still embedded
  in the file are skipped, since the original stands for them. A box
  imported from the file is written anyway, and its `imported_annot` goes
  to `annotate_pdf`'s `replaced`.
- `_drop_replaced(page, pnum, keys)` removes from one page each shown,
  non-reply `/FreeText`, `/Text` or `/Stamp` (`REPLACED_TYPES`:
  `TEXT_BOX_TYPES` and a picture's stamp) with a replaced
  `annotation_key`, with its popup and thread. `annotate_pdf` runs it on
  every page before it writes anything, and `page_with_ink` on the one
  page it draws. The reason: the viewer hides the file's own annotations
  by default, so the page shows Gamma's box, and an edit made here must
  reach the export.
- `highlight_note_text`, a highlight's or ink group's popup, follows sheet
  ancestry. It leaves out a box on a PDF page with the notes under it,
  since that box is its own `/FreeText`. A box under a sheet stays a line.
- **The switches.** A box is in the annotation layer and it is the user's
  writing, so either switch writes it. Only both off, the stored file,
  leaves the boxes out; ink and highlights need `highlights=1`. A picture
  follows the box's switches.

**The `/FreeText` keys, and why:**

- `/IT /FreeText`, the plain text-box intent. Not `FreeTextTypeWriter`: ISO
  and Acrobat spell it differently, and Acrobat's typewriter has no fill.
- `/Rect`: the drawn box, grown to its text, through the view-box- and
  rotation-aware `viewer_point_to_pdf`. The view box (`page_frame`) is what
  pdf.js, pdfium and MuPDF show: the crop box clipped to the media box.
- `/Contents`: `plain_text`, with a Zotero box's blank lines (`gaps`).
  `/DA`: `/Helv <size> Tf r g b rg`, which
  pdf.js, MuPDF and Acrobat read to rebuild the look. No AcroForm `/DR`:
  adding a form to a PDF without one has side effects.
- `/C`: the fill, only when `bg` is set; Acrobat, MuPDF and pypdf read it as
  the background. `/IC` is not FreeText's.
- `/BS << /W 0 >>`: no border. `/F 4`: print. `/NM` from the block id, `/T`
  the author (and `/Zotero:AuthorName`, the key Zotero reads it from), `/M`
  the block's `updated_at`.
- `/Rotate` on a turned page: Adobe's key, read by pdf.js and MuPDF, keeps
  a rebuilt look upright. NoRotate is not set: the appearance's matrix
  already keeps the text upright, and NoRotate support varies.
- `/AP /N`: a form typeset by `text_box.pdf_ops`, `/BBox` `[0 0 w h]` in
  display orientation. Its `/Matrix` turns it back against `/Rotate` (90:
  `[0 1 −1 0 h 0]`, 180: `[−1 0 0 −1 w h]`, 270: `[0 −1 1 0 0 w]`).
- `/GammaTextBox`: `{"v":1,"md":…,"box":…,"text":…}`, for a lossless
  re-import. `text` is the `/Contents` written, so the import can tell
  another viewer's edit from a reference label.
- `/Zotero:Rect`, `/Zotero:Rotation`, `/Zotero:FontSize`, `/Zotero:Color`:
  without them Zotero (7+) drops a `/FreeText` instead of importing it as
  its text annotation (pdf-worker's `readRawAnnotation`;
  [research/zotero-annotations.md](../research/zotero-annotations.md)).
  The rect is the drawn box as a Zotero box (`zotero_frame` of the box at
  its drawn `w × h`), in points, unturned, around that frame's centre: a
  Zotero box's is the drawn box itself, and a Gamma box's its text area,
  `[x + 4, y + 4, w − 8 (fixed) or w (auto), (h − 8) × 1.12]`, so Zotero
  puts the text where Gamma draws it, at its width. Like `/Rect`, it is
  not kept inside the page. The rotation is the page's: Zotero draws the
  text turned by the page's rotation less this one, so it stands upright,
  and its own writer rebuilds a `/Rect` from the two. The colour is the
  text's; Zotero would take `/C`, the fill, without it. With the `/NM`,
  that is all Zotero needs; it shows the plain `/Contents` in its own font.
- Not written: `/RC` and `/DS`, which could disagree with the appearance,
  and `/Q`, 0 by default. Nor `/RD`, since a rebuilt look would fill only
  inside it, or `/CL` and `/LE`, which are for callouts.

**Import** (`_text_box_from_annotation`; the endpoint and its rules for
replies and hidden annotations are in
[import_export.md](import_export.md#importing-annotations-embedded-in-a-pdf)):

- A `/FreeText` or `/Text` becomes a text box. Its place comes from `/Rect`
  through `pdf_point_to_viewer`, the exact inverse of the export's mapping,
  and `text_box.keep_on_surface` keeps it inside the page.
- With `/GammaTextBox`, the Markdown, size and style come from it (its
  `zotero` key with them) and `x`, `y` from `/Rect`, so a move in another
  viewer holds. When `/Contents`
  (`\r` read as `\n`) differs from the `text` written, `/Contents` wins.
- Other text is plain text: `markdown_of` escapes it, so "$5 and $10" reads
  as the other app showed it.
- A foreign `/FreeText` is a fixed box over its text area, the `/Rect` less
  the `/RD` margins. Its size and colour come from `/DA`, its fill from `/C`,
  its text from `/Contents`, else `/RC` reduced by `_rc_text`.
- A Zotero text annotation (`/Zotero:Rect`, no `/GammaTextBox`) is a
  [Zotero box](#zotero-boxes) at its rect: a PDF Zotero exported, or its
  copy in an RDF import. It may be turned, which a box is not: it is that
  unturned rect upright, on its centre (`_zotero_rect`).
- A `/Text` sticky note is a note-yellow auto box at its icon's top-left,
  sized by `measure`.
- The key (`annotation_key`: page, subtype, the rounded corners of the
  `/Rect`) is the one a highlight block made from the same annotation
  carries. Such a block counts as the box, so nothing comes twice.
- Only text boxes use the rotation-aware inverse. The highlight, area and
  ink import does a plain media-box flip, which agrees with the export
  wherever the view box is the media box.

**The Notability import** ([import_export.md](import_export.md#notability-notes);
`notability_map.text_box`) makes two kinds of box, each fixed-width, as
large as Notability drew it and kept on its surface (`keep_on_surface`):
a text box (a line per paragraph, list items as Markdown, the size and
colour of most of its characters, its paper colour as `bg`) and a formula
(display math in its colour). A turned box stands upright on its centre.
A Notability picture is a picture block (`notability_map.picture_block`),
not a box. A box holding one picture that an older import made stays a
box; the Notability export still writes it as a picture.

**Notebook PDF** (`notebook.notebook_pdf`, [notebooks.md](notebooks.md)):
each sheet's boxes with text are page content, selectable text, after the
paper and its pictures and before the ink. `notebook.placed_ops` draws the
pictures and boxes. The AI's picture of a PDF page draws them through it
too (`pdf_export.page_with_ink`), under the ink.

**A Zotero link** (`gamma/zotero_annotations.py`,
[zotero_sync.md](zotero_sync.md#text-boxes)): a box on a PDF page, not on
a sheet, is a `text` annotation of the page's PDF in Zotero, its text
plain and its rect the box, a [Zotero box](#zotero-boxes); a Gamma box is
made one the first time it goes. Zotero's `text` annotations come here as
Zotero boxes, a turned one upright on its centre as the import makes it,
and its `note` annotations as Gamma boxes at the icon in note yellow. What
one side holds that the other cannot (a turned annotation, a box's
Markdown or fill) never reads as a change on either side.

**The other readers** treat a box as a note and never as an area
highlight, placed by the nearest-sheet rule.

- The text exports ([import_export.md](import_export.md#text-boxes-in-the-exports))
  keep a box with `highlights=0` and drop it with `notes=0`. For the
  annotated PDF `notes=0`, the default, means "don't paint the notes", not
  "leave out my writing".
- The agent's `read_block` and the chat context label a box by where it is
  (`ai_context.placed_label`, which labels a picture the same way;
  [ai_tools.md](ai_tools.md), [ai_context.md](ai_context.md)). A walk that
  starts at a block asks `ai_context.under_sheet` once.
- `ai_context.area_highlight` never answers for a box. `move_block` refuses
  to move a box or a picture to another page unless its sheet moves too
  (`ai_tools._loose_placed`). A search hit on a box has `kind:
  "text_box"` (the block table's generated `kind`); the search panel lists
  it with the notes.

## Zotero boxes

A box with `zotero: true` is laid out as Zotero's reader lays out a text
annotation, so a box synced with Zotero breaks its lines at the same words
in both apps on one machine. The reader draws a text annotation as a
`<textarea>` exactly as wide and tall as its rect, with no padding, in its
body's font stack at `line-height: normal`, and its rect is the text's own
extent ([research/zotero-text-layout.md](../research/zotero-text-layout.md)).
A Zotero box is that textarea's twin.

- **Geometry.** No padding (`text_box.pad_of`, `textBox.js` `padOf`), so
  `x`, `y`, `w`, `h` are the text area and equal Zotero's rect, which maps
  to the box and back as the identity, to hundredths of a point
  ([zotero_sync.md](zotero_sync.md#text-boxes)).
- **On screen** (`.textBox.zotero .textBoxBody` in `markup.css`): padding
  0, the reader body's stack copied verbatim (`-apple-system,
  BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Helvetica, Arial,
  sans-serif`, `zotero_font` in `tests/shared/textbox.json`),
  `line-height: normal`, and the values a textarea resets that a style
  around the box could change (weight 400, normal style, normal letter and
  word spacing, no indent or text transform, start alignment). It wraps by
  the box's `overflow-wrap: anywhere`, which is what the textarea's
  `word-break: break-word` computes to, a line per source line.
- **Read mode** renders `shownText(content, box)`: what Zotero shows of the
  text `plain_text(…, gaps=True)` sends it. The stored text and
  click-to-source are unchanged.
  - Blank lines at either end are dropped, as `plain_text` and `_layout`
    drop them.
  - A line of only what Python's `str.strip()` removes, a no-break or
    full-width space among it, is blank, as the server reads it.
  - Each blank line between two others takes a line. The renderer already
    shows every blank line of a run but its first, the paragraph break
    (`editor/mdMarks.js` `expandBlankLines`), so a paragraph holding a
    no-break space goes before each run. Fenced code (```` ``` ```` and
    `~~~`, as the renderer reads them) and display math are left as they
    are.
  - A bare URL is split by an empty `<span>` after its `http`, `https` or
    `www`, so it stays text rather than becoming a link chip, and a link
    labelled with its own URL reads as its label. Nothing is inserted in
    code, math, a raw HTML tag, a link's destination or a reference
    definition.
  - A link's label, a `[[ref]]` and a citation draw as their text, inline,
    with no chip around it; a written link still opens with Ctrl+click.
- **Placing** (`placeBox(…, {zotero: true})`). While the workspace has a
  Zotero link that is not detached (`text.zotero`), a box made on a PDF
  page is a Zotero box from its first keystroke, so it is measured in
  Zotero's face and never changes face later. A tap puts its text at the
  pointer, its first line `size × ZOTERO_LINE` high (1.4 em, 19.6 pt at
  14 pt); a drag spans the drag. A box on a sheet never goes to Zotero and
  stays a Gamma box. This holds under receive only too, since the box goes
  once the link is two-way.
- **Width** (`refitBox`). An auto Zotero box is drawn at its text's width,
  as any auto box is, and after a local edit stores that width plus
  `ZOTERO_SLACK` (1 pt, as Zotero's `measureWidth` adds 1), up to the
  page's edge. Zotero has no auto width: it wraps at that width, where
  every line fits as here. A fixed Zotero box keeps its `w`; the layer
  measures its `h` at the screen's line height, which on one machine is
  Zotero's.
- **On the server** (`_layout`, `measure`, `pdf_ops`): no padding, and
  each blank line between two others a line. Rows stay `LINE` high in
  Helvetica's widths, so the PDF's appearance can break otherwise than the
  screen where a line nearly fills the box; Zotero reads `/Zotero:Rect`,
  not the appearance. `measure` gives the text's own size, and a rect the
  server makes for Zotero allows `ZOTERO_LINE` a line (`zotero_frame`).
- **Which boxes.** A box that comes from Zotero (the link's text
  annotation, the import of a Zotero `/FreeText`), a box placed while a
  link is live, and any other box the link sends to Zotero, which it
  converts first. A box keeps the key when the link goes, and when it is
  copied, exported or moved to another workspace: its look is its own. A
  workspace with no Zotero link makes Zotero boxes only by importing a PDF
  Zotero wrote.
- **What does not match.** Markdown (bold, headings, lists, code, math)
  renders here and goes as plain text, so it can move a break. A `~~~`
  fence is code here and text in Zotero. An @mention draws as its chip,
  while Zotero gets the raw `@…`; an id-only `[[ref]]` shows its id, as
  `plain_text` sends it. Leading spaces and runs of spaces, which Zotero
  keeps, collapse here. A bare URL is not clickable in read mode. The
  editor shows the source with `white-space: break-spaces`, so a box can
  wrap otherwise while it is typed in; read mode is what matches. A fill
  hugs the text, since there is no padding; Zotero shows none.
- **Platforms.** The match holds where both apps resolve the stack to the
  same face at the same width. It is measured on Windows only, Chromium
  against Chromium (Segoe UI), by the browser suite's reference textarea.
  macOS (SF), Linux (a Helvetica clone through fontconfig) and the iPad
  (whose Zotero app may not draw PDFs with the web reader) are not
  verified, and neither is Zotero desktop's Gecko engine. Zotero desktop
  7.0 to 10.0.x pads its textarea 2 CSS px and so wraps 4 px narrower than
  its rect; Gamma follows the web library and Zotero's reader since. Two
  machines whose stacks resolve to different faces cannot match, as
  Zotero's own clients do not.

## Tests

- `frontend/tests/textBox.test.mjs`: the shared cases, `textBoxesBySurface`
  (nested sheets, a stale `pdf_page`, identity), `textStyle`, the geometry.
  For a Zotero box: the cases of `tests/shared/zoterotext.json` (its
  items, `refitBox`'s slack, the merge), `placeBox`'s `zotero` option, and
  a pin of `markup.css`'s Zotero rule (padding 0, `line-height: normal`,
  the `zotero_font` stack). `textBoxShown.test.mjs`: `shownText` on its
  own, and the cases of `tests/shared/zoteroshown.json` rendered through
  react-markdown with the app's plugins, each with the lines it shows, its
  text and no link.
  `blockHistory.test.mjs`: the labels, folds, runs and the key-by-key
  rebase. Also `blockOps.test.mjs` and `collabSession.test.mjs`
  ([collab.md](collab.md#testing)). `placed.test.mjs`: `withPlaced` and
  `addPlaced`, which a box and a picture share.
- `backend/tests/test_text_box_merge.py`: the merge through `/ops` and
  `PUT /blocks/{id}`, a writer with no base box, removal, the mirror's diff.
- `backend/tests/test_text_box.py`: the `/FreeText` keys and their
  geometry at every rotation, with a MuPDF pixel check, the layout and
  the round trip to 0.01 pt. Also edits in another viewer, foreign boxes,
  sticky notes and threads, replaced originals, the switches and the
  notebook PDF. For a Zotero box: `zoterotext.json`'s layout, frames,
  rect round trip and merge, `/Zotero:Rect` of both kinds at every
  rotation, a Zotero `/FreeText`'s import, and blank lines in `/Contents`.
  `test_text_box_shown.py`: `zoteroshown.json` from the server's side, the
  lines `_layout` gives and the text `plain_text(gaps=True)` sends.
- `backend/tests/test_shared_fixtures.py`, and label checks in the export,
  Obsidian, Zotero, notes PDF, block and AI tool tests.
- Browser: `textBoxes.mjs`, group `textbox`
  ([debugging.md](debugging.md#browser-end-to-end-suite)). `select.mjs`
  runs it for changes under `frontend/src/markup/`, `ink/`, `notebook/`,
  `editor/`, `pdf/` and `backend/gamma/text_box.py`. Its step "a Zotero
  box wraps as Zotero's reader" puts `zoterotext.json`'s boxes on a page
  as Zotero boxes and as Gamma boxes, with blank lines, a URL and a sweep
  (10 sentences, widths 40 to 300 pt in steps of 13, sizes 10 and 14), and
  compares each Zotero box's lines with a hidden textarea styled by the
  reader's `.textAnnotation` rule, at 100% and 150%. The reference resolves
  the same stack as the box, so the step holds on any machine.
  `zotero-link.mjs` checks a synced text annotation and a box typed on a
  linked paper ([zotero_sync.md](zotero_sync.md#tests)).

## Not built yet

- The iPad app neither draws nor makes text boxes; a box is a note row
  there ([ipad.md](ipad.md)).
- Nothing drops `pdf_page` when a box moves under a sheet in the notes, or
  sets it when a box moves back to a PDF page's top level. A box moved to
  another page keeps its `pdf_page` and shows on that page of the new paper.
- Undo: after a selected box or picture is deleted with the hand armed and
  the strip open, Ctrl+Z steps the strokes. A run of differing changes (a
  move, then a restyle) is one step, named for the first.
- A press on an empty new box's own row keeps the box even when it opens no
  editor (its marker, a drag of the row).
- On a read-only page a stroke shows no pointer cursor, since the strokes
  take no pointer there.
- Tables, images and to-dos look different on screen and in the PDF. On
  macOS the screen's Helvetica sits about 0.08 em higher than the PDF's.
- The menu does not re-place itself when the strip opens, closes or grows.
- The annotated PDF is checked with MuPDF and pdf.js, not in Acrobat or
  macOS Preview, which may draw its own look from `/Contents`, `/DA`, `/C`.
- A box's child notes, a sticky note's replies among them, are not in the
  annotated PDF: a FreeText's `/Contents` must be the text it shows.
- With *Keep originals* (not the default), a box imported from the file and
  deleted here leaves its embedded original in the export: nothing records
  a deleted block. The default strips the original at import, so it goes.
- Import drops a callout's line and arrow. A foreign text's leading
  spaces, and its blank lines outside a Zotero box, take no room in a box
  and leave the next `/Contents`.
- A reply's text is kept as written, as a highlight's comment is, so
  Markdown-looking text there renders as Markdown.
- A `/Text` or `/FreeText` stored as a highlight block stays one, which the
  AI and the Logseq export read as an area highlight; there is no migration.
- In Zotero a box is plain text in Zotero's font, size and colour,
  wrapped at its rect's width: no Markdown, math or fill, and Zotero 6
  drops it. A Zotero box matches Zotero's lines on Windows; macOS, Linux
  and the iPad are not verified ([Zotero boxes](#zotero-boxes)). The Zotero
  note a box becomes without a PDF copy labels it by its page only as the
  note's top block. The Obsidian export does not say which sheet a box was
  on.
- Other fonts, alignment, borders and rotated boxes: the model has none.
