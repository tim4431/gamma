# Pictures on the page

A photo, a screenshot or a figure placed on a PDF page or a sheet of paper,
like a picture in Notability or GoodNotes. A picture is a block: its content
is its caption, Markdown like any note, and one property, `picture`, places
it. It is moved, scaled, stretched, turned, cropped, flipped and faded in
place, and the stored file never changes. The reasoning and the plan are in
[research/pictures.md](../research/pictures.md).

Code: `frontend/src/markup/` (`picture.js`, `usePictures.js`,
`PictureLayer.jsx`, `pictureUpload.js`, `TransformHandles.jsx`, `placed.js`,
`markup.css`, and the parts shared with text boxes and handwriting:
`MarkupLayers.jsx`, `MarkupToolbar.jsx`, `PageTools.jsx`), the lasso's half
in `frontend/src/ink/` (`InkLayer.jsx`, `selectionEdits.js`,
`useInkSelection.js`), the iPad app's
shared images (`frontend/src/transfers/openedFiles.js`, the document types
in `ipad/project.yml`), `gamma/picture.py`
(the server's rules and the picture in a PDF), the `/Stamp` writer in
`gamma/pdf_export.py`, the reader in `gamma/routers/imports.py`, the images
in `gamma/pdf_image.py` (an SVG drawn by `gamma/svg_draw.py`),
`gamma/notebook.py` `placed_ops`, and, for the
Notability and GoodNotes imports, the picture storing they share
(`gamma/note_app_import.py` `AppImport.store_picture`) and the mapping it
goes through (`gamma/notability_map.py` `picture_block`). Tests are listed
[below](#tests).

## What the user sees

The [user guide](../user_guide/user_guide.md#draw-and-type-on-the-page) walks through it.
These are the rules the code keeps.

### Adding a picture

- **The Picture button** sits on the markup strip after the Text tool, and
  `I` presses it while the strip is open (`P` is the pens' key). It is a
  button, not a tool that stays armed: it opens the system file picker
  (`image/*`, several files at once) and leaves the armed tool as it was. A
  read-only page has no button.
- **Paste** (Ctrl+V, ⌘V) with image files on the clipboard while no text
  field has the focus puts them at the centre of the surface in view. The
  handwriting clipboard's own copy (marked `data-gamma-ink`) stays the
  strip's paste, and a paste into a block editor stays the editor's.
- **Drop** image files on a page or a sheet: they land at the drop point. A
  drag that carries anything else stays the app's, which makes files into
  blocks. Over a surface the app's drop overlay and a row's outline stay off.
- **Shared to the iPad or Android app.** A JPEG, PNG, GIF, WebP, HEIC, HEIF
  or SVG that the share sheet, Files' *Open in* or AirDrop hands the iPad
  app, or a share or an *Open with* the Android app, lands as a picked one
  does ([ipad.md](ipad.md#opened-files), [android.md](android.md) "Opened
  files"). A share of images alone never goes to another workspace, which
  would open on its library.
- **The surface in view** takes a pick, a paste and a shared image. It is the
  notebook's current sheet, else the PDF page in view, else the sheet among
  the notes that shows the most of itself. With none, a pill says to open a
  PDF page or a notebook page first. For a shared image it names the file,
  and the app lets the file go. A shared workspace's question is not asked
  then.
- **Size and place.** A new picture keeps its natural aspect, fits inside
  half the surface's width and height, is never larger than its pixels as
  points, and is centred on the drop point or the surface, kept on it.
  Several files land one after another, each 12 CSS px right of and below
  the one before.
- **Selected as it lands**, unless the selection changed while it went up
  (another picture, a box or a lasso selection selected, a tool armed, the
  picture let go of) or a text field has the focus. Then it and the rest of
  the drop land unselected, so a box being typed in stays open.
- **What goes up.** PNG, JPEG, GIF and WebP up to 24 MP go up as they are,
  and a GIF keeps its animation. An SVG goes up as it is too, at the size
  its root element gives ([SVG pictures](#svg-pictures)). The browser
  decodes anything else it can read (HEIC and HEIF in Safari and the iPad
  app; TIFF, BMP, an SVG whose size the server cannot read), and that, or
  anything larger, is drawn on a canvas, downscaled and sent as a JPEG. A
  format that may hold transparency goes as a PNG. The original is not
  kept.
- **The canvas bounds a re-encode**: 16 Mi px and 8192 px an edge in
  desktop Chromium and Firefox, 8 Mi px and 4096 px an edge in WebKit
  (Safari, the iPad app) and on a touch-first device
  (`shared/lib/canvasSize.js`). A file kept as it is still goes up to 24 MP.
- **A file this browser cannot read** (HEIC in Chrome or Firefox) gets a
  pill saying to convert it to JPEG or PNG first. A file the server refuses
  gets "The picture could not be uploaded." The other files of the drop
  still land.

### Selecting and moving

- **Selecting.** With the hand armed or the strip closed, a click selects a
  picture, over a link too. A right-click selects it, which opens its menu.
  A double click opens crop mode. The caption is typed in the picture's row.
- **One selection.** At most one picture, one text box or one lasso
  selection is held in the app; selecting one lets go of the others.
  Arming a tool or opening another page lets go of the picture. So does a
  press anywhere but on the picture, its handles, its menu or the strip.
- **With a tool armed.** An ink tool armed makes the pictures inert, and ink
  draws over them. The selected picture still takes its presses, so one
  placed while a pen is armed moves before the pen writes over it. With the
  Text tool armed, a press on a picture that is not selected places a box.
- **Moving.** A drag over 6 CSS px moves a picture and selects it. A mouse
  or a pen drags any picture. A finger drags only the selected one, the only
  one with `touch-action: none`, and scrolls elsewhere.
- A second contact makes any press a pinch, which moves nothing.
- **Keys**, while a picture is selected and no text field has the focus:
  the arrows nudge it 1 pt, 10 with Shift; Delete or Backspace deletes it;
  Escape lets go ([hotkeys.md](hotkeys.md#widget-keys-the-markup-strip-and-a-text-box)).
  A locked picture's arrows and Delete do nothing ([Lock](#lock)).
- **Kept on the page.** Every move, resize, turn, crop, Fit and Reset keeps
  the turned frame on the surface, since the PDFs cut a picture at the
  page's edge. A wide picture turned on its side at the edge so has the
  corner of its own frame off the page.

### The handles

The selected picture has the lasso's two handles on its turned frame,
turned with it. They keep their screen size at any zoom.

- **Resize**, bottom right: a scale about the centre, the aspect as it is
  (a stretched picture stays stretched). No side goes under 8 pt, and the
  turned frame stays within the surface.
- **Alt + resize** stretches: the frame's width and height follow the
  pointer along the frame's own axes, as Notability lets a picture be
  stretched.
- **Rotate**, top right: a turn about the centre. Within 3° of a right angle
  it snaps to it, and the iPad plays its `snap` haptic as it does. With
  Shift it steps by 15°.
- **A focused handle** takes the arrow keys: a scale by 1.1 or a turn by
  15°.
- **Escape during a drag**, of a handle or of the picture itself, takes
  back the drag alone: the picture goes back where it was and stays
  selected, and nothing is stored. A press elsewhere mid-drag lets go of the
  picture and of the drag's preview.

### The menu and crop mode

The menu has the lasso's and the text box's look and placement: Crop, Flip
horizontal, Flip vertical, Duplicate, Copy image (where the clipboard takes
an image), More and Delete. More holds Fit to page, Reset, Bring to front,
Send to back, Opacity, Lock and Show note, and stays open for the session.

- **Duplicate** makes a copy with the caption, not the notes under it, 12
  CSS px right and down, over the original, selected.
- **Copy image** puts a PNG of the picture as it shows on the clipboard: the
  crop at its own pixel density, flipped and turned, with clear corners.
  An SVG is drawn at two pixels a point at least. The opacity is the PNG's
  alpha, so a paste elsewhere looks as the page does.
- **Fit to page** makes the turned frame as wide as the page less 24 pt each
  side, shrunk to the page's height less 24 pt each side when needed, and
  centres it.
- **Reset** brings back the whole image, upright and unflipped, at its
  natural aspect, as wide as it was and about the same centre. It keeps
  the opacity.
- **Opacity** opens a row of four steps under the menu: 100 %, 75 %, 50 %
  and 25 %, the picture's own pressed. The image shows at that opacity on
  the page, in its card and in the zoom window; its outline does not.
- **Bring to front** and **Send to back** set the picture's `z` one above
  the highest or one below the lowest of the surface's other pictures. Its
  row stays where it is in the notes ([Model](#model), "Order").
- **Show note** shows the block in the notes.
- **Crop mode** opens from the menu or a double click (a finger's double
  tap). The whole image shows dimmed in the picture's frame, placed,
  flipped and turned as the picture is, at full opacity. The part kept is
  bright, with a handle at each corner and side.
  - A handle moves its edges, at most to the image's edge and never closer
    than 1 % of the image to the edge across. A drag inside moves the crop.
  - Enter or the check applies it. The part kept stays where it was on the
    page at the same scale, and the frame shrinks or grows to it, so a
    stretch is kept.
  - Escape, the cross or a press outside cancels.
  - The crop's handles are 12 px with a 28 px hit area, on every device.

### Lock

**Lock** in More keeps a picture where it is, as Notability's and
GoodNotes' locks do. A locked picture is stored with `locked: true`.

- On the page it takes no drag, double click, handle, crop, flip, Fit,
  Reset or opacity. Its arrow keys and Delete do nothing, and a finger over
  it scrolls the page.
- The opacity is no geometry, but it waits for an unlock too. Notability's
  lock lets nothing change the picture, and the PDF's Locked flag keeps a
  viewer from changing a stamp's properties. A locked picture still shows
  at its opacity.
- A click, a tap or a right-click still selects it. Its menu is one row:
  Unlock, Copy image and Show note.
- Delete, Duplicate, Bring to front and Send to back are left out.
  GoodNotes puts its lock in Delete's place, and Notability's lock lets
  nothing change the picture. The block's row in the notes still deletes it.
- The caption is still typed in the picture's row.
- Every edit `usePictures` makes through `change` leaves a locked picture
  as it is, and so does `orderPicture`. So a drag that ends after someone
  else locked the picture stores nothing.
- The lasso never takes a locked picture ([The lasso](#the-lasso)).

### The lasso

The lasso takes pictures with the strokes it circles, as GoodNotes' does
([handwriting.md](handwriting.md#what-the-user-sees), the lasso).

- **What it takes**: an unlocked picture with more than half of its turned
  frame inside the loop or the box. The frame is weighed at five by five
  points, as a stroke is taken by more than half its samples
  (`picture.js` `lassoTakes`).
- **A tap** with the lasso selects the nearest stroke. Where no stroke is,
  it selects the picture under it, as a loop round it would: the topmost
  whose turned frame holds the point, unless that one is locked
  (`picture.js` `pictureAt`). Strokes lie over pictures, so a stroke under
  the tap wins. A finger's tap with *Draws with: Pen only* does the same
  while the lasso is armed. With the hand armed a click still selects the
  picture itself, with its own menu and handles.
- **Select all** (Ctrl+A, More → Select all) takes every unlocked picture
  of the surface with its strokes (`selectAllPictures`).
- **Pictures** in the lasso's row (this browser's `inkLassoPictures`, on to
  start) chooses whether the loop, a tap and Select all take them.
- **A locked picture** is never taken. One locked after it was taken, or
  gone to another surface since, drops out of the selection, and no edit
  moves it (`selectedPictures`).
- **The selection is the lasso's.** Its dashed box takes in the pictures'
  turned frames, and its handles and menu are the strokes'. The pictures
  it holds get no picture menu, handles or keys of their own.
- **Move, scale and turn** carry the pictures by the strokes' own values:
  a drag's shift, and the handles' scale and turn about the box's centre
  (`transformPicture`). A focused handle's keys do the same.
- While a drag or a handle is held, the pictures follow its preview
  (`carriedPicture`).
- Each picture is kept on the surface, and no side goes under 8 pt. Its
  crop, flips, lock, opacity and `z` stay.
- The handles scale the pictures uniformly, aspect kept. Alt stretches a
  picture on its own handle only: the lasso's handle never stretched with
  Alt, so it has no stretch to carry.
- **Per action**:
  - Move, resize, rotate, the flips, Delete, Duplicate, Copy and Cut take
    the pictures too.
  - A flip mirrors each picture about the box's centre line: its centre
    mirrored, its image flipped and its turn reversed (`mirrorPicture`).
  - Duplicate puts each copy just over its original, as far off as the
    strokes' copies, and selects the copies (`duplicatePicture`).
  - Copy and Cut put the pictures on the handwriting clipboard with the
    strokes ([Copy, cut and paste](#copy-cut-and-paste)).
  - Color, Width and style, New note, Merge into note, Select note and
    Convert (Transcribe in the chat, Redraw as shapes, Convert to text,
    Copy as SVG and Download SVG) act on the strokes alone; the SVG
    leaves the pictures out. The pictures stay selected, except after
    Redraw as shapes, which selects the new shapes alone, and Convert to
    text, which lets the selection go
    ([handwriting_ai.md](handwriting_ai.md)).
  - A selection of pictures alone greys those buttons and Comment.
  - Show note shows the first group's note, else the first picture's.
- **One step to undo.** A lasso step that changed pictures keeps its
  strokes in the stroke history and its pictures in the block history.
  Both entries carry the step's link.
  - The strip's Undo and Redo step both.
  - Ctrl+Z with the strip closed steps the block history, and the strokes
    with it.
  - Both pills name it "selection move", "selection resize", "selection
    rotation", "selection flip", "selection deletion", "selection
    duplication", "selection cut" or "selection paste", the stroke entry's
    label.
  - Convert to text makes the same linked step of the strokes it removes
    and the text box it adds, "ink conversion to text"
    ([handwriting_ai.md](handwriting_ai.md#one-step-for-both)).
  - The two histories stay two, linked rather than merged: each half
    undoes on its own once the other history has moved on, and the pill
    says which half stayed ([Not built yet](#not-built-yet)).
- **Collab** carries both halves as any edit: the pictures through
  `setBlocks`, the strokes through the ink save.

#### Copy, cut and paste

The lasso's clipboard is the handwriting one
([handwriting.md](handwriting.md#what-the-user-sees), "Copy, cut and
paste"), and a selection's pictures ride on it with its strokes.

- **Copy** (Ctrl+C, the menu) keeps each selected picture as it is placed,
  with its caption, not the notes under it, as Duplicate copies one. The
  box a paste places takes in their turned frames. The pill counts both:
  "Copied 2 strokes and 1 picture."
- **Cut** (Ctrl+X, More → Cut) copies, then removes both halves as one
  step, through the link Delete uses ("selection cut"). The notes under a
  cut picture stay, in its place in the notes, since the paste brings back
  only the picture and its caption; Delete takes them with it.
- **Paste** puts the copies down as the strokes' are: the copied box at the
  centre of the surface in view, at a held pen's or a right-click's point,
  kept on the surface. Each picture keeps its place against the strokes
  and the other pictures, its crop, flips and opacity. The copies go over
  the surface's pictures in the order the originals stacked, and become
  the selection with the strokes. Strokes and pictures are one step of
  both histories ("selection paste"); a paste of strokes alone is still
  "ink paste".
- **The file.** A picture names its upload, `/api/uploads/<hash>.<ext>`
  in the workspace's own store. A pasted copy names the same upload, as a
  Duplicate does, on any page or sheet of the workspace. A cut picture's
  file stays stored for 30 days after nothing names it
  ([user_db.md](user_db.md#stored-files)), so a paste after a cut finds
  it.
- **Another workspace or server** never gets the paste. The clipboard is
  this tab's memory, and switching workspaces or servers reloads the tab,
  so a paste is always in the workspace whose uploads its pictures name.
  The system clipboard's copy carries the pictures in its JSON for other
  apps, but no paste reads it back.
- Copy image, on a picture's own menu, stays a PNG on the system
  clipboard, which a paste places as a new picture.

### Undo, the notes and other views

- **Undo.** While a picture is selected, Ctrl+Z and the strip's buttons step
  the block history, not the strokes. Changes to one picture alone less than
  a second apart are one step. The history names a picture's creation,
  deletion, caption edit, move, resize, rotation, flip, crop, lock, unlock,
  opacity change and order change (Bring to front, Send to back). A run of
  several kinds is a "picture change". A lasso step that moved pictures is
  one step of both histories ([The lasso](#the-lasso)).
- **In the notes** a picture is a row with a picture marker. Its text is the
  caption, and a card under it shows the picture as placed (cropped, flipped,
  turned and at its opacity), at most 320 × 240 CSS px. The marker and the
  card show it on its page and outline it; Show note goes the other way.
- A picture under a sheet shows no `p.N` in the notes. A picture whose file
  is not named says "Picture missing" in its card and is drawn nowhere.
- **Copy as markdown** on a picture's row copies its `![caption](url)` line.
- **Read-only** (a view share, the Viewer role), a click on a picture shows
  its note. In a share the file is read through the share token.
- **An edit share's visitor** adds pictures too. The surface takes a drop
  itself, and the fresh upload reads back through the share at once
  ([Server and interchange](#server-and-interchange), "Share reads").
- **On a dark PDF page** a picture is not inverted, as ink and boxes are,
  but dimmed to 88 % brightness. A highlighter drawn across it multiplies
  into it as on a light page, though on the page around it the
  highlighter screens, and so it does on dark notebook paper, where
  elsewhere a highlighter is drawn as it is. Where the picture is
  transparent, or its image has yet to load, the highlighter does as on
  the page ([handwriting.md](handwriting.md#client), the planes). Over a
  faded picture it takes both blends, in the picture's opacity's
  proportion. On a light page, where both are a multiply, it multiplies
  once into the picture and the page showing through it.
- **Replay.** A picture carries `t0`. Under a playing recording that covers
  it, it is shaded until its moment, and a click plays from there
  ([recording.md](recording.md)).
- **The zoom window** shows the page's pictures under the handwriting, as
  the page draws them, dimmed on a dark page and shaded in a replay. There
  they take no press, so a pen writes over them, and a highlighter
  multiplies into them as on the page. A drag on the page shows there once
  it lands.

## Model

No new endpoint. A picture is a block with `picture`, plus `pdf_page` (the
1-based page) on a PDF page and `t0` from its making. A picture imported
from the PDF's own `/Stamp` also carries `imported_annot` and
`annot_stripped`, as a text box does. Migration step 44 teaches the block
table's `kind` column the new kind (below).

| `picture` key | meaning | default |
|---|---|---|
| `url` | the stored file, `/api/uploads/<stem>.<ext>`: a stem of 1 to 128 letters, digits, `-` and `_`, and `png`, `jpg`, `jpeg`, `gif`, `webp` or `svg` in any case (`URL_RE`, `PICTURE_URL`) | required: without it the value is no picture |
| `x`, `y` | the top-left of the frame before the turn, from −`MAX_COORD` to `MAX_COORD` | 0 |
| `w`, `h` | the frame's size: the picture as shown after the crop and before the turn, at least 8 pt (`MIN_SIDE`); an aspect other than the crop's is a stretch | 144 (`DEFAULT_SIDE`) |
| `angle` | the turn about the frame's centre in degrees, clockwise as seen (CSS `rotate`, y down), in [−180, 180) | 0 |
| `crop` | `{x, y, w, h}` as fractions of the image as the browser shows it, each side at least 0.01 (`CROP_MIN`), inside the image | the whole image, `{0, 0, 1, 1}` |
| `flip_h`, `flip_v` | mirrored about the frame's own vertical or horizontal axis | false |
| `locked` | kept where it is: the page neither moves nor edits it ([Lock](#lock)) | false, and then left out |
| `opacity` | how strongly the image covers what lies under it, from 0.01 (`MIN_OPACITY`) to 1 | 1, and then left out |
| `z` | its place among the surface's pictures, higher over lower, from −`MAX_Z` to `MAX_Z` (10⁹) ([Model](#model), "Order") | 0, and then left out |

`MAX_COORD` is a text box's bound, 14 400 pt. On a PDF page the frame is the
one ink files use ([handwriting.md](handwriting.md#the-stroke-file-gamma-ink-v2));
on a sheet it is points from the sheet's top-left. Everything that draws a
picture applies the crop, then the flips, then the turn: the CSS, the PDF
matrix and the Notability writer.

- **No pixel size is stored.** The crop is in fractions, so each reader
  takes the size from the file: the screen from the `<img>`, the PDF
  writers from the image XObject, the Notability writer from the header.
  An SVG's size comes from its root element ([SVG pictures](#svg-pictures)).
- **The image as the browser shows it** has its EXIF orientation applied by
  the browser's rule ([Server and interchange](#server-and-interchange),
  "EXIF orientation").

**Normalization.** Every reader goes through `normalize_picture`
(`gamma/picture.py`) or `normalizePicture` (`markup/picture.js`). A bad key
takes its default, unknown keys are dropped, and a value that is not an
object, or whose `url` is no picture upload, is no picture. The two sides
must agree on these details:

- Numbers are clamped and rounded to hundredths, halves up, as `Math.round`
  rounds (Python's `round` goes to even). A whole number stays an integer.
- A non-finite number takes the default, not the bound. So does an integer
  too long for a double, which `JSON.parse` reads as Infinity.
- −0 reads as 0, in a corner and in a turn.
- The turn wraps with `math.fmod`, which is JavaScript's `%`, then rounds.
  A turn that rounds to 180 is −180, and 359.996 is 0.
- The crop is rounded to ten-thousandths before it is clamped. Its sides
  are then clamped to 0.01 … 1, and its corner is pulled in so the crop
  stays inside the image, then rounded again.
- Only a real boolean is a flip: "true", 1 and null do not flip.
- Only a real `true` locks. An unlocked picture has no `locked` key.
- An opacity is clamped to 0.01 … 1 and rounded to hundredths. A picture
  at full opacity has no `opacity` key, and one that is no number reads as
  1.
- A `z` is clamped to ±`MAX_Z` and rounded to hundredths. One at 0 is left
  out, and one that is no number reads as 0.

`tests/shared/picture.json` pins both sides, with the cases of
`keep_on_surface`, the turned corners, the Markdown line and the stacking.
`test_shared_fixtures.py` compares
JSON text, as the text box cases do.

**What a picture block is.** `is_picture` and `isPicture`: a `picture`
object on a block with no `ink_url` key and no text box. That is the order
the `kind` column tests them in: ink, text box, picture, sheet, recording,
link, highlight. A picture whose value does not normalize is still a picture
block, of kind `picture`: its card says the file is missing and it draws
nothing. A picture is no highlight (`highlights.is_highlight`,
`blockModel.isHighlightBlock`), and `blockPage` reads its `pdf_page`.

**Placement: the nearest sheet wins**, the text box's rule
([text_boxes.md](text_boxes.md#model)). A picture on a PDF page is made at
the page's top level, and one on a sheet as the sheet's last child
(`markup/placed.js` `addPlaced`). Every reader places it the same way:

- under a sheet, it is on the nearest sheet above it, whatever `pdf_page`
  it carries ([notebooks.md](notebooks.md));
- otherwise it is on its `pdf_page` when that is a positive integer;
- otherwise it is on no surface, and shows only in the notes.

The client's `picturesBySurface(tree)` keeps the tree's own objects, so an
unchanged picture keeps its identity. The server's
`picture.picture_page(props, on_sheet)` takes `on_sheet` from the writer's
own walk of the tree. An import keeps a picture on its surface with
`keep_on_surface`: scaled down about its centre when its turned frame is
wider or taller than the surface, then moved so that the turned frame lies
on it. `keepOnSurface` is the twin.

**A replaced PDF.** Replacing a page's PDF keeps a picture's `pdf_page` and
frame as they are, as for a text box: only the highlights are found again in
the new file, and carrying the pictures along with them is open
([highlight_relocation.md](highlight_relocation.md#limits-and-open-items)).

**Order.** A surface's pictures stack by `z`, then in document order where
`z` is equal (`stackOrder`, `stack_order`). A picture without `z` is at 0,
so pictures that carry none stack in document order, as text boxes do.

- Bring to front sets `z` one above the highest of the surface's other
  pictures, and Send to back one below the lowest (`orderPicture`). The
  block stays where it is in the notes.
- A picture already on top, or at the bottom, is left as it is, and so is
  a locked one. So is one whose `z`, clamped at ±`MAX_Z`, would still tie
  behind the top or the bottom one.
- A new picture goes in last in document order and takes the top
  picture's `z` when that is above 0 (`onTop`), so it goes over every
  picture on its surface. A duplicate keeps the original's `z` and goes in
  right after it, so it lands just over the original.
- Every reader of the stacking goes through the helper: the page layer
  (its drawing order and so its hit test), the zoom window, the
  highlighter's masks, the annotated PDF, the notebook PDF, the AI's page
  picture and the Notability export.
- The server reads document order through `notebook.document_order`: each
  block before its children, siblings by position.
- The imports set no `z`. A PDF's stamps and a note's media come in in the
  order they stack, so document order stacks them. Stamps imported onto a
  page whose pictures carry a `z` above 0 stack under those pictures.
- `z` orders pictures among themselves only. On every surface the pictures
  lie under the text boxes, and both under the ink.

**The `kind` column** gains `picture`, tested after `text_box`
([user_db.md](user_db.md)). Migration step 44, `picture_kind`, is a
workspace part only: `normalize.block_columns` finds the column's stored
definition outdated and rebuilds it, as steps 29, 30 and 38 did
([migrations.md](migrations.md)). `SCHEMA_VERSION` and `workspace_format()`
are 44. `blocks_store.block_kind` agrees with the column, and the search
panel labels a hit "picture". A restore of a copy stamped below the base,
a scoped Gamma export among them, runs `normalize.highlight_shape`, which
keeps a picture's `pdf_page` as it keeps a box's.

**Merge.** Every change sends the whole `picture`. Taken as one value,
one person's crop would undo another's move. So a `set` of `picture` whose
`base_props` names the picture it was changed from merges, as a text box's
does ([text_boxes.md](text_boxes.md#model)).

- `merge_picture(stored, mine, base)` is the stored picture with the keys
  the writer changed from its base, taken from `mine`. Keys compare
  normalized, so a default spelled out is no change, and the crop compares
  by value. Where both changed a key, the later writer wins.
- **A key left out at its default** (`locked`, `opacity` and `z`, listed
  in `_OMITTED` and `OMITTED`) compares as that default. So an unlock, a
  return to full opacity or a `z` back at 0, which drops the key, is a
  change, and a lock and a move made at once both land.
- **The frame merges as its size and its centre.** A resize, a turn and a
  crop rewrite `x` and `y` too, about the centre or about the part kept.
  Merged key by key, a resize made while someone moved the picture would
  bring the old top-left back and undo the move.
  - `w` and `h` merge key by key.
  - When the stored frame is the base's, the writer's frame is taken whole.
  - A centre the writer did not move (a resize, a turn, a flip) stays the
    stored one. A shift under 0.0075 pt is no move: a resize about the
    centre rounds `x` and `y` to hundredths.
  - Two moves, with no crop on either side: the writer's centre.
  - Otherwise the writer's shift of the centre goes onto the stored centre.
    So the part a crop kept stays where it is on a picture moved meanwhile,
    and a picture cropped meanwhile moves as far as the writer moved it.
- A writer that names no base replaces the picture: an import, the agent's
  tools, a `PUT /blocks/{id}` without `base_properties`.
- `mergePicture` (`markup/picture.js`) is the twin, and
  `tests/shared/picturemerge.json` pins both.
- **One table on each side** lists the keys merged this way, each with its
  normalizer and merge: `ops.PLACED_KEYS` on the server and `blockOps.PLACED`
  on the client, `text_box` and `picture`. `ops.py` merges a `set` by it, and
  `PUT /blocks/{id}` with `base_properties`. The mirror's diff names the
  base (`sync_tree._set_op`), and its resend of a lost push
  (`sync_engine._placed_again`; [mirror.md](mirror.md)) judges a picture
  by the parts the merge merges (`picture.resend_picture`): each key but
  `x` and `y` on its own, then the centre as one. So a lost resize sent
  again after someone moved the picture stays about the moved centre. The
  browser names the base in `diffTrees`, meets a remote change with its own still on the
  way (`collabSession.js` `ahead`, `storedPlaced`; [collab.md](collab.md)
  "same-box merge"), and rebases its undo entries (`rebaseHistory`).

## Client

`markup/` holds what a page surface carries. A surface is a PDF page number
or a sheet id, the key ink and text boxes use. The parts shared with them
are in [text_boxes.md](text_boxes.md#client) and
[handwriting.md](handwriting.md#client).

- `picture.js`: the pure rules and the geometry. Its relative imports carry
  `.js`, so node loads it.
  - The rules: `normalizePicture`, `isPicture`, `mergePicture`,
    `picturesBySurface`, `turnedCorners`, `turnedBox`, `keepOnSurface`.
  - The edits, each normalized and each keeping the turned frame on the
    surface: `placePicture`, `movePicture`, `scalePicture`, `stretchPicture`,
    `turnPicture` (the magnet and Shift's 15°), `flipPicture`, `cropTo`,
    `fitPicture`, `resetPicture`. `turnPicture`, `cropTo` and `resetPicture`
    keep it on the surface only when given one. `lockPicture` sets or takes
    off the lock and `fadePicture` sets the opacity. Every edit keeps them
    and the `z`.
  - Crop mode's `imageDelta` (a screen drag as fractions of the image,
    through the turn and the flips) and `dragCrop`; the z-order's
    `stackOrder` (which `picturesBySurface` lists each surface by),
    `orderPicture`, `onTop` and `duplicatePicture` (the menu's Duplicate
    and the lasso's); the drawing's `pictureLayout`.
  - The lasso's: `lassoTakes` (what a loop takes, through `ink.js`
    `pointInPolygon`), `pictureAt` (what a tap takes), `transformPicture`
    (the handles' scale and turn, through `ink.js` `transformPoint`),
    `mirrorPicture` (a flip about the selection's centre line) and
    `carriedPicture` (a picture as the selection's live preview shows it).
  - An SVG's: `svgSize` (twin of `picture.svg_size`), `svgFill` (its
    stretch) and `isSvgUrl` ([SVG pictures](#svg-pictures)).
  - `captionText` and `pictureMarkdown`, the `![caption](url)` line, which
    read the caption's Markdown as the words it shows (`captionPlain`, the
    twin of `text_box.plain_text`).
- `PictureLayer.jsx`: one surface's pictures.
  - `PictureLayer` is a size container over the surface, its pictures placed
    in its percentages, so a zoom needs no re-render. It takes the image
    drags and drops (below).
  - `PictureView` draws one picture: the frame placed and turned by
    `rotate(angle deg)`, an inner box that clips to the frame and mirrors by
    the flips, and the `<img>` sized and offset so the crop fills the frame,
    at the picture's opacity. A stretch is the frame's aspect against the
    crop's, so `object-fit` is never needed. An SVG's `<img>` reads its
    natural size as it loads (`useSvgNatural`), for `svgFill`. A turned
    element takes pointer events only inside its turned box, so the browser
    does the hit test.
  - `PagePicture` is a picture on the page: the press, the move, the keys,
    the handles' values (`handleValue`), the menu's actions and crop mode.
    It is memoized, and the page's marks slice keeps its identity while its
    pictures are the same (`useMarks`), so a page re-renders only for its
    own pictures. A locked one (`.locked`) starts no press, double click
    or handle, and its arrow keys and Delete do nothing.
  - `PictureLayer`'s `carry` is the lasso selection's live preview
    `{ids, value}`, which `MarkupLayers` keeps from its ink layer's
    `onCarry`. Each picture it names draws carried (`PagePicture`'s
    `carried`), and so do `StillPictures`' in the zoom window.
  - `PictureMenu`, `PictureCrop` and `PictureCropBar` are the menu, crop
    mode and its two buttons. The menu uses the lasso menu's classes and
    placement (`useSelectionMenuAnchor`, `InkTooltips`). A locked picture's
    menu is its short row (`.pictureLockedMenu`); `shared/ui/Icons.jsx`
    draws `LockIcon` and `UnlockIcon`. Opacity (`ContrastIcon`) opens a row
    of the `OPACITIES` steps under More, `inkSegBtn` pills in an
    `.inkEditOptions` row, as the lasso menu's Width and Style open theirs.
  - `PictureCard` draws the picture in the notes (`editor/BlockTree.jsx`),
    in its turned frame's box.
  - `StillPictures` draws a surface's pictures and nothing more, for the
    zoom window's copy of a page: a `PictureView` each, taking no press and
    shaded in a replay. It is memoized on the pictures' ids and properties,
    so the panel's renders for a stroke or a move of the window draw none
    of them again.
- `usePictures.js`: App's side. It owns the selection `{id, cropping}`, the
  flash, the pictures by surface and every edit, made through `setBlocks`,
  so the op diff sends it and the history records it. A new picture is an
  undo step of its own.
  - `change(id, fn)`, the `onPictureChange` action, leaves a locked picture
    as it is, the menu's opacity steps among its edits.
    `onPictureLock(id, on)` is the menu's Lock and Unlock, and
    `onPictureOrder(id, where)` its Bring to front and Send to back.
  - `pick()` is the Picture button and its key: `shared/lib/pickFiles.js`
    opens the picker ([ui-design.md](ui-design.md)), and there is no file
    input in App.
  - A `paste` listener on the document takes image files when no text
    field has the focus.
  - `place(files, surface, size, at)` uploads the files one after another
    and makes each a block (`addPlaced`) over the surface's pictures
    (`onTop`).
  - `placeInView(files)` places the files on the surface in view, at its
    centre, for the picker and the iPad app's shared images. It resolves
    false, placing nothing, when no surface is in view.
- `pictureUpload.js`: the upload and the re-encode (below), the drag tests
  (`imageFiles`, `onlyImages`, `picturesDrag`) and Copy image's `shownPng`,
  which draws the picture at its opacity.
- `TransformHandles.jsx`: the handles a lasso selection and a picture share
  (below).
- `placed.js`: what a text box and a picture share as objects on a surface:
  `withPlaced(tree, id, key, fn)`, one placed value's edit, and
  `addPlaced(tree, surface, id, properties, content)`, where a new one
  goes, `content` a pasted picture's caption.
- `MarkupLayers.jsx` mounts the picture layer, `MarkupToolbar.jsx` has the
  button (`onPicture`), and `PageTools.jsx` carries the `onPicture…`
  actions among the page's actions.
- `ink/stripKeys.js` maps `I` to `{kind: "picture"}`, and
  `ink/useStripKeys.js` calls `onPicture`.
- `app/App.jsx`:
  - `pictureTarget()` is the surface a pick or a paste lands on: `pasteTarget()`
    (`ink/useInkSurfaces.js`), else the sheet among the notes that shows the
    most of itself (`visiblePart`).
  - `openOpenedHeld` hands the images the iPad app was given
    (`transfers/openedFiles.js` `openedKind` "picture") to `placeInView`,
    before the PDFs it uploads: a page opened meanwhile would stop their
    placing. Those that find no surface are named in the status.
    `placeOpenedFiles` keeps a share of images alone in the workspace
    shown, and `placeOpenedHeld` lets such a share go before the question
    when `pictureTarget()` finds no surface.
  - `boxUndo` and `hasBox` cover a selected picture: the strip's buttons and
    Ctrl+Z go to the block history, and Escape to the picture.
  - `stepBlocks(link, fn)` is the lasso's edit of its pictures, which marks
    the tree it commits with the step's link (`stepLinksRef`, the block
    history's `linkOf`). `stepBlockHistory(redo, inEditor)` is every step
    of the block history: one that answers a `link` steps the strokes'
    half too (`useInkDocument.js` `inkUndo(redo, link)`). `reportUndo`
    then names the step by the stroke entry's label, or says the
    handwriting stays (`inkStayed`) when that entry is not the newest.
  - `showOnPage(id)` scrolls to a picture's turned box and flashes it.
  - The app's drop overlay stays off over a surface (`picturesDrag`).
- `editor/BlockTree.jsx`: a picture's row draws its marker
  (`.pictureMarker`, beside ink's and a box's) and `PictureCard`. `onSheet`
  goes down the recursion, so a picture under a sheet shows no `p.N`. A
  row's dragover leaves its outline off for images over the sheet it draws.
  Copy as markdown writes `pictureMarkdown` for a picture.
- `editor/blockHistory.js`: `describeTransition` names a picture's making,
  deletion and caption edit, and `pictureChange` a change by the keys it
  changed: "picture lock" and "picture unlock" for `locked` alone, "picture
  opacity change" for `opacity` alone, "picture order change" for `z`
  alone. `observeTree` folds changes of one
  picture's `picture` alone into one step within `EDIT_MERGE_MS`
  (`boxChange`).
  `rebaseHistory` carries another client's picture onto each entry through
  `PLACED`. A tree whose `linkOf` names a lasso step is an entry of its
  own that keeps the link, and passes it to the redo or undo entry its
  step makes. `undoStep` answers the link of the entry it restored, and
  `undoLinked(s, o, link, redo)` steps the newest entry only when it
  carries `link`.
- `ink/`: the lasso's half ([handwriting.md](handwriting.md#client)).
  `InkLayer.jsx` takes the pictures a loop circles and the one a tap is
  on (`lassoPictures`, `tappedPicture`), draws the selection's box round
  them and reports its preview (`onCarry`). `selectionEdits.js` has
  `selectedPictures`, `selectionBox`, `editPictures`, `removePictures`,
  the pictures' half of `actionEdit`, `selectAllPictures`, and the
  clipboard's: `clipOf` (the pictures with their captions), `clipStatus`
  (the pill) and `pastePictures` (paste's half in the tree).
  `selectedPictures` is the one rule for the selection's pictures: those
  its surface draws, none locked since. The layer's box and every edit
  read it. `useInkSelection.js` makes each step
  (`stepSelection`; a paste's link goes through `useInkStrokes.js`
  `handleInkStrokes`), and `useInkDocument.js` and `inkHistory.js` keep its
  `link` (`takeEntry`, `picturesStayed`).
- `shared/model/blockModel.js`: `isHighlightBlock` refuses a picture, and
  `blockPage` reads its `pdf_page`. It tests for one itself
  (`placesPicture`): importing `picture.js` would be a cycle.
- `shared/lib/copyImage.js` `copyPng` makes Copy image's `ClipboardItem` in
  the click, so Safari keeps the gesture while the PNG is drawn.
- `markup.css`: the layer, the frame, the outline, a locked picture's
  cursor and touch, the flash, crop mode and the card. `app.css`: the
  marker.

**The selection rules, shared with text boxes and the lasso:**

- Selecting a picture makes App let go of a box (`onBoxDeselect`) and of the
  lasso's selection (`clearInkSelection`). Selecting a box or making a
  lasso selection calls `pictures.deselect()`.
- The pictures a lasso takes are its selection's ([The lasso](#the-lasso)),
  never `usePictures`' selected picture.
- Every change of the selection moves a counter in `usePictures`: a
  picture selected or let go, a tool armed, and `deselect()`, which App
  calls when a box or a lasso selection is selected. An upload that lands takes the
  selection only when the counter is where it was as its upload began and
  no text field has the focus. Once one file lands unselected, the rest of
  its drop does too.
- A tool armed, another page opened, or the selected picture no longer
  drawn (undone, deleted, moved off its surface, by anyone) lets go of it.
- `markup/TextBoxLayer.jsx`: with the Text tool armed, its capture-phase
  claim skips `.pagePicture.selected, .pictureHandle, .pictureCrop`.
- `ink/InkLayer.jsx` `NOT_INK` gains `.pagePicture.selected` and
  `.pictureCrop`, so a press there starts no ink, a stylus's included.
  The handles are `.inkTransformHandle` and so were in it
  already.

**TransformHandles.** `markup/TransformHandles.jsx` is the lasso's two
handles, lifted from `ink/InkLayer.jsx`: two 28 px buttons laid over the
surface at `positions` (its points), so they keep their screen size and
follow the live preview. A drag previews (`onPreview(value)`, null when it
ends) and its release commits (`onCommit(value)`).

- `InkTransformHandles` wraps it for the lasso, whose value is `{cx, cy,
  angle, scale}`.
- A picture passes `valueFor(raw)`, which reads the press itself
  (`handleValue`: `scalePicture`, `stretchPicture` with Alt, `turnPicture`
  with the magnet), and `onStep(mode, sign)` for the arrow keys. `turn`
  turns both handles with the frame, each about its corner; `labels` and
  `className` (`pictureHandle`) name them.
- Escape is taken in the capture phase while a drag is on, and stops there,
  so the picture's own Escape never sees it. A blur cancels a drag too.
- Handles removed mid-drag, as when the selection goes, end the preview
  (`onPreview(null)`).
- `pct`, `ARROWS` (the arrow keys' steps) and `arrowSign` live here; the ink
  layer and the text-box layer import them.

**The upload and the re-encode** (`pictureUpload.js`):

- `encodePlan(type, w, h)`: a PNG, JPEG, GIF or WebP of at most `MAX_PIXELS`
  (24 000 000) is kept. Anything else is drawn at a size within
  `MAX_PIXELS` and `canvasSize`'s caps, as a JPEG (quality 0.9), or a PNG
  when it came as PNG, WebP, GIF or SVG.
- `preparePicture` keeps an SVG whose root element `svgSize` reads, at that
  size, before `encodePlan`: the file read is its first `SVG_HEAD` bytes.
  An SVG it cannot read (no SVG namespace on its root) is drawn again as
  a PNG, since the server would draw none.
- `decode` uses `createImageBitmap` with `imageOrientation: "from-image"`,
  and an `<img>` for what it refuses (an SVG). The natural size it gives is
  the size as shown.
- `redraw` paints white under a JPEG, so what was clear is not black. It
  gives null when there is no canvas context (WebKit refuses one once the
  page's canvases hold its memory) or the encode throws, and it always
  releases the canvas.
- `uploadPicture(file, post)` answers `{url, natural}`, or `{error:
  "decode"}` or `{error: "upload"}`, and never rejects. `post` is
  `transfers/FileChip.jsx` `postFile` (`POST /api/upload-image`), which
  shows the upload in the background tasks. An answer whose `url` is no
  picture upload is an upload error.
- `picturesDrag(e)`: image files only, over `.pdfPageWrap` or `.nbSheet`.
  `onlyImages` reads a dragover's items, since its files cannot be read
  yet.

**The layers.**

- `MarkupLayers.jsx` mounts the picture layer before the text-box layer and
  the ink layer, all three at z-index 6. So pictures lie under the boxes and
  the ink, above a PDF page's highlights (2), note badges (4) and link boxes
  (5). The zoom window's copy of a surface (`chrome` false) mounts
  `StillPictures` in the picture layer's place, under its ink layer and
  over the page's pixels. `.pictureLayer.stillPictures .pagePicture` takes
  no pointer.
- `PictureLayer` takes an image-only dragover on an editable surface itself,
  so an edit share, whose app takes no files, takes the drop. The drop is
  taken in the capture phase on the surface's wrapper, so the app's drop
  and a sheet row's never see it.
- The selected picture's handles and crop are portalled into the surface's
  wrapper at z-index 7, as the lasso's handles are. A box or a stroke
  drawn over them takes none of their presses.
- A press on a picture stops there, so the page's own drags (an area note)
  never start on one.
- With an ink tool armed, `.inkArmed .pagePicture:not(.selected)` takes no
  pointer.
- `ABOVE_PASSIVE_INK` gains `.pagePicture`: on a read-only page a tap on a
  picture is not a tap on a stroke.
- The dark-page filter in `app.css` inverts ink and `.textBoxBody` only.
  `markup.css` dims a picture's image to `brightness(0.88)` on a dark page,
  in the viewer and in the zoom window's panel (`.inkZoomPanel.pdfDark`,
  Gamma Dark).
- `MarkupLayers` passes the surface's pictures to its ink layer, the zoom
  window's copy's too. On a surface with pictures each highlighter
  plane is drawn through a mask of the surface less its pictures. Its
  strokes are drawn again, a `<use>` of them, through an alpha mask of the
  pictures' images in `svg.inkOverPictures`. That SVG always multiplies,
  and neither the dark-page filter nor a `[data-blend]` rule reaches it.
  `ink/overPictures.js` `pictureShape` places each image there as
  `PictureView` does: the crop, then the flips, then the turn. A faded
  picture's image (`.inkPictureMask`) is whole in the mask on a light
  page, where one multiply is exact. Where the page's blend is another, a
  rule beside each such `[data-blend]` rule (`app.css`, `notebook.css`)
  sets the image to the picture's opacity (`--picture-opacity`). There the
  two blends share the highlighter. The masks follow the pictures as stored: while a
  picture is dragged, sized or turned they keep its place until the change
  lands. The stroke in
  progress and peers' wet highlighters keep the page's blend over a
  picture until they land.

## Server and interchange

- `gamma/picture.py`: the rules above, and the picture in a PDF.
  - `normalize_picture`, `is_picture`, `picture_page`, `stack_order`,
    `merge_picture`, `resend_picture` (the mirror's resend, above),
    `turned_corners`, `turned_box`, `turned_size`, `keep_on_surface`.
  - `pdf_ops(pic, name)` draws the image XObject `name` where the screen
    shows it, in a y-down frame of the surface: from the frame's centre,
    turned, mirrored about the frame's axes, clipped to the frame, and the
    whole image scaled so its crop fills the frame. It takes no pixel size:
    the crop is in fractions and an image fills the unit square. Below full
    opacity it sets the graphics state ink names by its alpha
    (`ink.pdf_alpha_op`, `/GSink<α × 1000>`). `ink.pdf_path_resources`
    declares that state from the operators, for the stamp's appearance,
    `placed_ops` and the notes PDF alike.
  - `caption_text` is the caption on one line. `markdown_of(content, pic)`
    is the `![caption](url)` line the text exports and the AI print: the
    caption read as text (`text_box.plain_text`), on one line, without
    brackets and pipes, escaped once (`escape_markdown`). So an imported
    caption that came escaped (`my\_photo.jpg`) is not escaped twice, and
    `**Bold** figure` reads `Bold figure`.
  - `image_size(data)` is a file's pixel size as the browser shows it, read
    from the header by the format's own Pillow plugin. Nothing is decoded,
    so a file of any declared size answers at once. An SVG's is `svg_size`.
  - `picture_type(data)` is the type of a file a picture shows: a PNG,
    JPEG, GIF or WebP by its first bytes (`ai_pictures.sniff`), an SVG by
    its root element. `svg_size` and `svg_sized` are below.

**EXIF orientation** is applied where Chromium and Firefox apply it
(`pdf_image.browser_orientation`): a JPEG's first APP1 Exif block, and a
PNG's `eXIf` chunk when it comes before the image data. A WebP's EXIF, a
late `eXIf` and ImageMagick's text-chunk profile are ignored, as those
browsers ignore them; Pillow's `exif_transpose` would apply them. So the
crop's fractions mean the same image on the screen, in the PDFs and in the
Notability export.

- The decoded path turns the pixels themselves.
- A verbatim JPEG or PNG keeps its bytes. With an orientation other than 1
  it is wrapped in a unit-square form XObject that draws it through the
  orientation, marked `/GammaOrientation`, and its size is reported as
  shown.
- `picture.image_size` follows the same rule.
- Uploads open only as `BROWSER_FORMATS`: `Image.open` would try every
  format Pillow knows, and an EPS hands its bytes to Ghostscript.
  `XObjectStore` logs a missing upload
  ([pdf_typesetting.md](pdf_typesetting.md#images)).

**The annotated PDF.** `routers/export.py` `_collect_pictures`, beside
`_collect_text_boxes` and `_on_sheets`, walks the page in document order,
keeps each picture that normalizes, is on a PDF page and has its file, and
lists them in the order they stack (`stack_order`).

- A picture under a sheet is on that sheet, whatever `pdf_page` says.
- A picture whose file is missing is left out with a log line, and its
  original, when the PDF still embeds one, stays.
- A picture imported from the file and still embedded in it is written
  anyway, and its `imported_annot` goes to `replaced`. `_drop_replaced(page,
  pnum, keys)` removes each shown, non-reply `/FreeText`, `/Text` or `/Stamp`
  (`REPLACED_TYPES`) with a replaced key, as for a box
  ([text_boxes.md](text_boxes.md#server-and-interchange)).
- **The order.** Annotations paint in the order they are written, so
  `annotate_pdf` writes them as the page stacks them: the highlights and
  area notes first, then the pictures, the text boxes and the handwriting.
  Pictures go in the order they stack, boxes in document order.
- **The switches.** A picture is the user's own work on the page, so any
  switch writes it, as it writes a box. Only all of them off, the stored
  file, leaves the pictures out.
- `highlight_note_text`, a highlight's popup, leaves out a picture on a PDF
  page with the notes under it: it is its own `/Stamp`. A picture under a
  sheet stays a line, its `![caption](url)`.

**The `/Stamp` keys, and why** (`pdf_export._picture_annotation`):

- `/IT /StampImage`: PDF 2.0's intent for a stamp that shows an image.
- `/Rect`: the turned frame's bounding box, through the view-box- and
  rotation-aware `viewer_point_to_pdf`, so a viewer places it where the
  screen does.
- `/AP /N`: a form whose `/BBox` is `[0 0 w h]`, the turned frame's size
  (`turned_size`). Its content draws `picture.pdf_ops` from that box's
  top-left, and its `/Matrix` turns it back against the page's rotation
  (`_upright`, a box's), so it stands as the screen shows it. A faded
  picture's form declares its opacity's `/ExtGState`.
- `/CA`: a faded picture's opacity, for a viewer that draws the stamp anew
  and for the import. A viewer that draws the appearance takes the
  appearance's alpha and not `/CA` again: pdf.js reads `/CA` only to build
  an appearance an annotation lacks, and MuPDF draws the stamp's alpha once.
- The image XObject comes from `pdf_image.XObjectStore`, so a JPEG and a
  plain PNG go in verbatim and the rest is decoded through Pillow, its
  transparency a soft mask. An SVG is a form of vectors in its place
  ([SVG pictures](#svg-pictures)).
- `/Contents`: the caption's plain text on one line, references read as
  their labels (`resolve_ref`), as a box's. An empty caption writes none.
- `/F 4`: print. A locked picture's stamp adds the Locked bit, 128
  (`pdf_export.LOCKED`), so a viewer keeps it in place too. `/NM` from the
  block id, `/T` the author, `/M` the block's `updated_at`.
- `/GammaPicture`: `{"v":1,"picture":…,"caption":…,"text":…}`, for a lossless
  re-import. `text` is the `/Contents` written, so the import can tell
  another viewer's edit from a reference label.
- No `/Zotero:*` keys: Zotero draws a stamp's appearance but imports no
  annotation for it. A picture is seen in Zotero, not kept by it.

**Import** (`routers/imports.py`; the endpoint and its rules for replies and
hidden annotations are in
[import_export.md](import_export.md#importing-annotations-embedded-in-a-pdf)):

- `/Stamp` is among `_IMPORT_TYPES`. A stamp makes a picture when the
  resources of its appearance (`/AP /N`, the state `/AS` names), its nested
  forms walked, name exactly one image and no font. A form a Gamma export
  drew an SVG through (`/GammaSVG`) counts as that image, its own fonts
  and content not walked. A rubber stamp of words or vectors makes nothing.
- **A Gamma stamp** (`/GammaPicture`): the picture, its size, crop, turn and
  flips, and its Markdown caption come from the record. Its place comes from
  the centre of `/Rect` through `pdf_point_to_viewer`, the inverse of the
  export's mapping, so a move in another viewer holds. When `/Contents`
  differs from the `text` written, `/Contents` wins, read as plain text.
- **The file of a Gamma stamp** is the one it was drawn from when the
  workspace has it at the same pixel size as the image read back. So a
  round trip keeps the file as it was, a GIF's animation with it. Otherwise
  the image read back is stored.
- **A foreign stamp** is the whole image where its appearance draws it
  (`_image_corners`), as a viewer draws an annotation: the `/BBox` through
  its `/Matrix` fitted to `/Rect`, then the `cm` of the form's content
  (`_Drawn`), then the page's turn. A NoRotate stamp stays upright, pinned at
  its `/Rect`'s top-left. `_frame_of` reads the place, the size, the turn
  and `flip_h` from the image's corners; a skew is lost. Its `/Contents` is
  the caption.
- `_Drawn` works out each form once, however many stamps share it, and reads
  at most 1 MB of appearance content per PDF. Past that, an image is taken
  to fill its form's `/BBox`.
- **The lock** of either kind is the Locked bit of its `/F`
  (`pdf_export.annotation_flags`), not the record's `locked`. So a lock or
  an unlock made in another viewer holds.
- **The opacity** of either kind is its `/CA` (`_alpha`, from
  `MIN_OPACITY` to 1), not the record's `opacity`, for the same reason. A
  stamp with none is at full opacity. The appearance's own alpha is not
  read, as for a highlight's or an ink's `/CA`. So a foreign stamp whose
  appearance is opaque comes in at its `/CA` all the same, fainter than
  pdf.js and MuPDF draw it.
- **The z** of the record is dropped. The stamps come in in the page's
  order, the order the export stacked them in, so document order stacks
  them as they were. They go in last in the notes, but at 0 they stack
  under a picture already on the page whose `z` is above 0, such as one
  brought to the front.
- Either kind is kept on the page (`keep_on_surface`). Its key
  (`annotation_key`: page, subtype, the rounded `/Rect`) is its
  `imported_annot`, so a second import adds nothing.

**The import reads an untrusted PDF's images within bounds**
(`pdf_image.image_file`, `_PictureFiles`):

- Files are read lazily: once per image XObject however many stamps draw it,
  only for the stamps this import adds, and before the write lock.
- One image decodes at most `MAX_DECODED_PIXELS` (40 Mpx), and
  `DecodeBudget` caps one import at `MAX_IMPORT_PIXELS` (200 Mpx).
- `stream_data` inflates a stream by hand, never past the size its image
  needs. pypdf's `get_data` would inflate it whole, up to its 75 MB cap, and
  cache the result.
- Each image is opened as the format its filter names (PNG, JPEG, JPEG
  2000), and one whose header disagrees with `/Width` and `/Height` is
  refused before it is decoded.
- A `/DCTDecode` image without a soft mask is the JPEG it holds, as it is
  when its EXIF orientation is the one the PDF drew it through (a Gamma
  export's). Otherwise its orientation tag is set to that one, or, with no
  tag, it is decoded.
- A Flate image with PNG row filters is rebuilt as the PNG it was, nothing
  decoded (`_as_png`). Anything else is decoded and written as a PNG, its
  `/SMask` the alpha. A `/DCTDecode` image with a soft mask so comes in as a
  PNG with its alpha.
- An SVG's form is the SVG its `/GammaSVG` holds, inflated up to
  `MAX_SVG_BYTES` and stored as an `.svg`.
- A stamp whose image does not read or is past the budget makes no
  block, with a log line, and stays in the file: the strip keeps it
  (`_strip_embedded_annotations(keep=…)`), for a later import to try
  again. Storing a picture meets one refusal, the storage quota
  (`_picture_url`): a full workspace (507) stops the import, nothing comes
  in and nothing is stripped. Replies go only under records that made a
  block.

**Notebook PDF** (`notebook.notebook_pdf`, [notebooks.md](notebooks.md)):
each sheet's pictures (`sheet_pictures`, in the order they stack) are page
content after the paper, then its text boxes over them, then the ink. `notebook.placed_ops` draws the
pictures and the boxes, and `pdf_export.page_with_ink` uses it too. Its
resources declare the pictures' opacities. A picture whose file cannot be
read is left out.

**The AI's pictures of a page with its handwriting** draw what the user
sees. `pdf_export.page_with_ink` takes the page's pictures and text boxes,
picked as the annotated export picks them (`ink_view._placed`), and draws
them into the page's content (`_stamp_placed`) under the handwriting. The
originals they replace leave the copy first, so each is drawn once.
`view_ink` goes through it, and so does `ai_pictures.render_region` with
`ink` (`ink_view.page_with_handwriting`): the page picture with `ink=1`, a
region with its handwriting. Without `ink` the picture is the PDF page
alone, as stored. A sheet goes through `notebook_pdf`.

**The AI** ([ai_tools.md](ai_tools.md), [ai_context.md](ai_context.md)):

- `ai_context.placed_label` names a text box or a picture by where it is:
  "(picture on p. N)", "(picture on a page of paper)", "(picture on the page
  of paper above)", "(picture, not placed on a page)".
- `picture_text` is the picture's `![caption](url)` line, which `read_block`,
  the notes focus and the page report print in place of its content.
- `view_image` on a picture block shows its own file first, its caption the
  alt text: the whole file, and how the page shows it otherwise
  (`_placed_as`: the crop as the part kept, the turn, the flips, the
  opacity).
- `move_block` refuses to move a picture to another page unless the sheet it
  is on moves too (`_loose_placed`). The delete card names the kind
  "picture".

**The text exports** ([import_export.md](import_export.md#pictures-in-the-exports))
write a picture's `markdown_of` line, its file bundled as a note's image is.
In the Markdown and Obsidian exports and the notes PDF a picture follows
the notes switch, as a box does: `notes=0` leaves it out, `highlights=0`
keeps it.

- Markdown: the line, then the page line a box gets (`` `p.N` ``) on a PDF
  page.
- Obsidian: the line, then the page link on a PDF page.
- Logseq: the line, with no page: Logseq keeps a page only on annotation
  blocks.
- Zotero: with the PDFs included, the copy draws the picture and its file
  rides as an item attachment. Brought back, that attachment is no chip:
  the page shows the picture already, or the copy's stamp brings it in
  with that file (`_zotero_extras`). A picture the copy carries is a note
  only when it has notes under it, as a box is. Without a copy it is a
  note: "Picture on p.N", the embedded image with the caption's plain text
  as its alt, then the caption's line, kept since Zotero does not show the
  alt (`zotero_export._shown`). The README says once that Zotero shows the
  pictures but imports no annotation for them.
- Notes PDF (`pdf_document._Canvas.picture`): the picture drawn as placed,
  at its opacity, its turned frame fitted to the column (never enlarged, at
  most a page tall), then a small grey "picture, p. N" line, then the
  caption. One that cannot be drawn is still named.
- The Gamma export and the backups carry the file by its property
  reference (`storage.upload_refs`), as an ink file is carried.

**Share reads.** A share visitor reads an upload only when a block of the
shared pages names it (`routers/uploads.py` `_pages_reference`). A fresh
upload has no saved block yet, so `upload_image` and `upload_file` remember
the yes at once for the uploader's share scope (`_share_reads_own_upload`,
the same key and `SHARE_READ_TTL_S` as a found one). The visitor sent those
bytes, so the yes shows it nothing it did not have.

**Search** indexes the caption as any content, and a hit's `kind` is
`picture`.

## SVG pictures

An SVG is stored as it came and drawn as vectors wherever the format takes
them. Its scripts run nowhere.

**Served without its scripts.** Every upload is served by one route,
`GET /api/uploads/{filename}` (`routers/uploads.py` `serve_upload`), on the
app, the share host and the page hosts alike.

- An SVG (`documents.formats.SANDBOXED_EXTENSIONS`, with HTML) opened at its URL
  downloads (`Content-Disposition: attachment`), so it never runs as a
  document in the app's origin.
- In case a browser draws it anyway it carries `Content-Security-Policy:
  default-src 'none'; style-src 'unsafe-inline'; sandbox`: no script, no
  fetch, an origin of its own. Its inline styles stay.
- `X-Content-Type-Options: nosniff` keeps its type.
- An `<img>`, or the highlighter mask's SVG `<image>`, runs no script of
  an SVG and fetches nothing it names. The page, the notes and the zoom
  window draw it so; its markup is never put in the DOM.
- The file is not sanitized. The headers defuse every script an SVG can
  hold at once (`<script>`, event attributes, `javascript:` links), and a
  round trip keeps the file byte for byte.

**Its size** is read from its root element, as Chromium sizes an `<img>`
of one (`picture.svg_size`, `picture.js` `svgSize`, pinned by
`tests/shared/picture.json`).

- `width` and `height` in absolute units give it, px when unitless. An em
  or a rem is 16 px, the browser's default font size, even in a file that
  sets another. A percentage, an ex, a ch or a viewport unit is no length.
- One of them alone takes the other from the `viewBox`'s aspect, else 150
  high or 300 wide. A viewBox alone is fitted in 300 × 150; neither is 300
  × 150.
- The longer side is at most `MAX_COORD`, each side at least 1, in whole
  pixels.
- The root element is looked for in the first `SVG_HEAD` (64 KiB) bytes,
  past an XML declaration, comments and a DOCTYPE. It must be an unprefixed
  `<svg>` whose `xmlns` is the SVG namespace, or the file is no SVG.
- The browser draws no such file either, except one with a prefixed root
  (`<s:svg xmlns:s=…>`). A drop re-encodes that file as a PNG, and the
  imports leave it out with a warning.
- Each pattern can match a text one way only, so a file of junk costs one
  pass. The server's patterns match ASCII alone, as the client's do.
- Placing, Reset and the crop's fractions use this size. The client reads
  it before the upload, the server from the file.

**On the page.** A browser keeps an SVG's own aspect inside the box an
`<img>` gives it, letterboxed, where every other image and the PDFs stretch.
So `svgFill` draws an SVG's `<img>` at its natural aspect and scales it
across to its box. A stretched SVG then fills its frame, in crop mode too.

**The PDFs** (`pdf_image.svg_xobject`, through `XObjectStore`): the
annotated PDF's stamp, the notebook PDF, the AI's page pictures and the
notes PDF draw an SVG as vectors.

- MuPDF lays it out as a page (`svg_pdf`). It reads no viewBox for a missing
  side and puts an SVG of no size on a Letter page. So it reads a copy sized
  by `svg_size` (`picture.svg_sized`).
- The page becomes a form XObject whose `/Matrix` maps its `/BBox` onto the
  unit square an image fills. `pdf_ops` draws it as it draws any image.
- The form's `/GammaSVG` holds the file itself. The import reads the SVG back
  from it, so the picture comes back as the SVG in any workspace.
- An SVG's text is drawn in MuPDF's fonts, which are embedded.

**Bounded before MuPDF sees it** (`pdf_image._check_svg`): MuPDF runs no
script, fetches nothing outside the file and expands no entity. What it
draws can still be unbounded, so these are refused first.

- A file past `MAX_SVG_BYTES` (8 MiB), or not well-formed.
- One that draws past `MAX_SVG_ELEMENTS` (500 000), each `<use>` counted as
  all it shows. A `<use>` of a group of two `<use>`s doubles the count at
  each step, and MuPDF would draw every copy.
- A loop of `<use>`s, which MuPDF walks along every path.
- An entity in an id or an `href`. MuPDF keeps it as written, so it names
  another element than the parsed tree shows (`_ENTITY_REF`).
- Embedded pictures past `MAX_DECODED_PIXELS` together, read from their
  headers. An embedded picture must be a PNG, JPEG, GIF or WebP: an SVG in an
  SVG would be weighed by no one.
- A refused SVG is left out of a PDF with a log line, as a missing file is.

A reference is weighed at the most MuPDF could draw for it. Every `href`
counts whatever its prefix, as MuPDF reads the literal `xlink:href` first.
A repeated id counts as its heaviest element, and a line break in an id
matches a space.

**Drawn under a time limit** (`pdf_image._svg_drawing`, `gamma/svg_draw.py`).
Inside those bounds MuPDF can still take minutes to draw an SVG, and nothing
stops a call into it. Measured on PyMuPDF 1.28: the conversion to a PDF is
quick (1.3 s for 8 MiB of path data), the drawing is not. At 2048 px a path
of 600 000 points took 190 s, 100 000 translucent layers over the picture
more than 5 minutes, 10 000 masks 13 s, a page of large text 9 to 25 s.
Tiny dashes, gradients of 100 000 stops, patterns, nested clips and a huge
viewBox drew at once; groups 500 deep do not open.

- MuPDF converts and draws an SVG file only in a process of its own
  (`gamma/svg_draw.py`). The process is killed past `SVG_DRAW_SECONDS`
  (10 s). Its address space is capped at `SVG_DRAW_MEMORY` (1 GiB; the 40 MP
  of embedded pictures the bounds let through take 300 MB) where the system
  holds a process to one, which is Linux. macOS and Windows have no such cap.
- The PDF (`svg_pdf`) is converted there and drawn once at `SVG_DRAW_SIDE`
  (2048 px on its longer side), to prove it draws in time. An AI picture
  of a whole page holding the SVG draws it smaller (at most
  `RENDER_MAX_SIDE`, 1568), and the Notability PNG, 2048 too, shares the
  drawing. A PNG of another side or of a region (`svg_png`: the AI's PNG,
  a picture page's region) is a drawing of its own, in its own process.
- The server holds no lock while the process works: other PDF requests go
  on. At most two such processes run at once (`_SVG_DRAWS`).
- An SVG that does not draw in time is a `ValueError`, as any SVG that does
  not draw, so every caller leaves it out as before: the PDFs with a log
  line, the Notability export counted, the AI told.
- A process takes 80 ms from a cold start, importing PyMuPDF included,
  against 20 ms for the same small drawing in the server. What a drawing
  came to (the PNG, or why it does not draw) is kept by the file's content,
  the side and the region, `SVG_CACHE_BYTES` (64 MiB) in all, the oldest
  going first. Only the drawing at `SVG_DRAW_SIDE` keeps the PDF too. A page
  exported again starts no process, and a file that does not draw in time
  costs its 10 s once until the server restarts.
- The process is the server's own Python running `svg_draw.py` as a script
  (`-P`, so nothing beside it shadows a module). The frozen desktop server
  has no Python, so it runs itself: `gamma-server --draw-svg <side> <cap>`
  (`desktop/backend_entry.py`). It reads the sized SVG on stdin, and a
  region as four more arguments. It writes the PDF's length, the PDF and
  the PNG on stdout. `subprocess` starts it: fork and exec in C on Linux and
  macOS, with no Python run between them in a threaded server, and
  `CreateProcess` with no console window on Windows.
- The iPad and Android apps run the server inside their own process, with
  no Python to start another, and iOS starts no process at all.
  `sys.platform` is `ios` on an iPad and its simulator and `android` on
  Android. There an SVG is converted and drawn in the server, under the
  PyMuPDF lock, with no time limit and nothing kept, as before
  (`SVG_APART`).
- An SVG is not drawn when it is added. The browser draws it on the page
  without MuPDF, and MuPDF reads less than a browser: a file it cannot draw
  in time is still a good picture on screen, and is left out only of what
  MuPDF makes. A check at upload would cost every upload a process and
  miss the files that come by other ways (the imports, a mirror, a clone).

**As pixels** (`pdf_image.svg_png`): MuPDF's page of it, or a region of
that page, drawn in the drawing's process, its longer side a given size,
clear where it draws nothing. In the mobile apps `pdf_text.page_raster`
draws it.

- The Notability export: Notability takes no SVG, so a picture of one goes
  in as a PNG of it, `notability_export.SVG_PNG_SIDE` (2048) px on its
  longer side. It comes back as that PNG. One that does not draw is left out
  and counted ("1 SVG picture could not be drawn…").
- The AI: a vision model takes no SVG, so `ai_pictures.read_stored` hands it
  a PNG of one, `RENDER_MAX_SIDE` on its longer side. `view_image` shows a
  note's SVG images so too, and says of one that does not draw that it does
  not read as a picture.
- A picture page's picture (`ai_pictures.image_region`: the chat's picture
  of a picture page or of its region, the feedback reader's): the region
  alone, its longer side `RENDER_MAX_SIDE`, zoomed at most 4, on white
  ([ai.md](ai.md#pictures) "A picture page's region").

**As it came.** The Notability and GoodNotes imports store an SVG picture
as its file (`note_app_import.picture_file`, `picture.picture_type`). The
Markdown, Obsidian and Logseq exports bundle the `.svg`, as they bundle a
note's images. The iPad app declares `public.svg-image`, so a shared SVG
lands as a picked one.

## The Notability import and export

[import_export.md](import_export.md#notability-notes) has the rules, and
[research/notability-format.md](../research/notability-format.md) the
format.

- **Import.** A picture becomes a picture block: on a PDF page with its
  `pdf_page`, on paper under its sheet (`notability_map.picture_block`). A
  PNG, JPEG, GIF, WebP or SVG is stored as it came and never decoded; only
  its header's size is read (`picture.image_size`, an SVG's root element),
  to turn the crop into fractions. A TIFF, BMP or AVIF is stored as a PNG
  of its first frame, turned as its EXIF orientation says
  (`note_app_import.picture_file`), up
  to `pdf_image.MAX_DECODED_PIXELS` in its pixels and in a TIFF's tile;
  one of floating-point samples, and any other file, is left out with a
  warning. The crop, the turn and the flips come in as numbers, an `.ntb`'s
  crop mirrored first on a flipped picture, since Notability measures it
  on the image as flipped. A crop past the image cuts the frame with it,
  and a frame with a side under `MIN_SIDE` grows about its centre. The
  caption is the content, its line breaks kept, each line escaped as plain
  text. A GIF is its animation whenever the note has it, cropped or not.
  When the note lacks a picture's image, its smaller copy stands in, else
  a GIF's animation alone, shown whole.
- **The lock** comes from a `.note`'s `locked` key, and from an `.ntb`
  block's lock (its field 20, an edit's field 17). The sample's two locked
  pictures come in locked.
- The GoodNotes import's pictures become picture blocks through the same
  storing (`note_app_import.AppImport.store_picture`): the frame (`#2`)
  through the page's, the turn (`#3.#3`, in radians) as `angle`, and the
  file as it came (a TIFF, BMP or AVIF as a PNG). A die-cut sticker's PDF
  is rendered to a PNG first, once for all the pictures that show it
  (`goodnotes_import._sticker`). A GoodNotes crop is not applied yet: the
  picture comes in whole, counted
  ([import_export.md](import_export.md#goodnotes-notebooks)). The image
  element as read holds no lock and no opacity, so a GoodNotes picture
  comes in unlocked and opaque.
- Each file is read and stored once per note, however many pictures show
  it. `copied_picture` takes Gamma's own copy of a picture back when the
  note still shows it as the copy has it, with the note's lock. The copy's
  opacity comes back with it, and its `z` does not: the media's `zIndex`
  order, the blocks' document order, stacks them.
- **Export.** A picture block becomes an `ImageMediaObject` with
  `rotationDegrees` from the turn, `FigureCropRectKey` the crop in the file's
  pixels and, for a cropped picture, the same rectangle as the path
  Notability keeps beside it (`$0`, `notability_write.crop_path`), the
  flips, `locked`, and the caption's plain text as `captionFieldText`. A
  surface's pictures go in the order they stack (`stack_order`), under its
  boxes, and both under its stickies, as on screen. An SVG goes in as a PNG
  of it ([SVG pictures](#svg-pictures)).
- **The opacity** has no key in a media object, in the sample or in the
  sources. Notability shows a faded picture opaque, and the export says so
  once per page ("picture opacity" in `LEFT_OUT`). Gamma's copy keeps it.
- Pictures imported before pictures were blocks stay text boxes holding an
  image. There is no migration: their pixels were transformed and the crop
  is gone. The export still writes such a box as a picture, which comes back
  a picture block.

## Tests

- `frontend/tests/picture.test.mjs`: the shared cases (`picture.json`'s
  normalize, keep, turned, markdown, svg_size and stack, and
  `picturemerge.json`), the constants against the server's, `isPicture`,
  `picturesBySurface` (a PDF page's in document order, a sheet's those no
  nearer sheet holds, each surface's by `z` first), and the
  geometry: `placePicture`, `movePicture`, `scalePicture`, `stretchPicture`,
  `turnPicture`, `cropTo`, `fitPicture`, `resetPicture`, `lockPicture`,
  `fadePicture`, `pictureLayout`, `svgFill`. An SVG's size reads at once
  whatever junk it holds.
- `frontend/tests/pictureEdits.test.mjs`: crop mode's geometry, the z-order
  (`orderPicture`'s `z`, the block left in place, a locked picture left
  as it is, a duplicate just over its original), the lasso (what a loop
  takes of an upright and a turned frame, never a locked one; what a tap
  takes, the topmost and never a locked one; the handles' scale and turn
  and a drag's move as the strokes' points go; a flip that shows the
  mirror image), the line a row copies
  as, what a file becomes before it goes up and what its upload answers
  (an SVG as it is, at `svgSize`), and which drags a surface takes.
- `frontend/tests/selectionEdits.test.mjs`: the lasso selection's
  pictures (on its surface, none locked since, its box round their turned
  frames, kept aside with them), their half of an edit and of Delete,
  which actions carry them, select all's, the clipboard's pictures and
  captions, its pill, and a paste's (the same upload, shifted with the
  strokes, over the surface's pictures in the order they stacked).
  `inkHistory.test.mjs`: a lasso step's `link`, the entry a linked undo
  takes, why its pictures stayed and its pill.
- `frontend/tests/placed.test.mjs`: `withPlaced` and `addPlaced`, a
  caption with it.
- `frontend/tests/overPictures.test.mjs`: where the highlighter's mask puts
  a picture, its crop's corners on `turnedCorners` as the flips mirror
  them.
- `blockHistory.test.mjs` (the labels, a run as one step, the key-by-key
  rebase, a lasso step's linked entry, left to a later undo after a note
  typed and blocked by someone's move), `blockOps.test.mjs` (the base a
  change names, the page and no highlight), `collabSession.test.mjs` (a
  remote picture meeting ours on the way, the stored picture brought back)
  ([collab.md](collab.md#testing)).
- `stripKeys.test.mjs` (`I`), `designTokens.test.mjs` (no coarse-pointer
  size rule but the text box handle's), `transferFormats.test.mjs` (the
  export summary and the import's text).
- `backend/tests/test_picture.py`: what a picture block is and its page, the
  caption and its line, and `image_size` from the header alone (EXIF as the
  browsers apply it, nothing decoded, a huge declared size answered at
  once), an SVG's by `picture_type` and read at once whatever junk it
  holds.
- `backend/tests/test_picture_svg.py`: an SVG served as a sandboxed
  download, the copy MuPDF reads sized as the browser shows it, the stamp's
  vectors drawn as the screen draws them (stretched too), the SVG read back
  by the import and kept in a round trip, the bounds MuPDF never passes
  (every way of writing a reference too), a drawing past its time killed
  with the PyMuPDF lock free meanwhile and left out, a drawing kept by its
  content, a region drawn alone in the process (a picture page's too), the
  mobile apps' drawing in the server, the frozen server's `--draw-svg`,
  nothing read outside the file,
  the Notability export's PNG (of an old box's SVG too), the AI's PNG and
  its word for one that does
  not draw, and the file as it came in a GoodNotes import and the text
  exports.
- `backend/tests/test_picture_merge.py`: the merge through `/ops` and `PUT
  /blocks/{id}`, a writer with no base, removal, a box and a picture in one
  batch, a lock beside a move, an opacity beside a flip, a `z` beside a
  move, the mirror's diff and its resend of a lost push, a lost unlock, a
  lost return to full opacity and a lost `z` back at 0 among them.
- `backend/tests/test_picture_pdf.py`: the `/Stamp` keys and place on turned
  and cropped pages with MuPDF pixel checks, highlights under pictures,
  document order and `z` (the export, the AI's page picture and a sheet's
  PDF), the stamps' order read back with no `z`, the re-import and edits in
  another viewer, a locked
  stamp and its `/F` read back, a faded stamp drawn once at its alpha and
  its `/CA` read back, a faded picture in the notebook, page and notes
  PDFs, foreign stamps
  (turned pages, NoRotate, placed by their appearance, a camera JPEG), the
  browser's orientation, the import's bounds (format, declared size,
  inflate, budget, the quota), the notebook PDF, `page_with_ink`, the text
  exports and their bundles, Zotero (the note's alt and caption line, the
  copy and the attachment, an export brought back making no chip of a
  picture), the notes PDF, and the AI's labels and tools.
- `backend/tests/test_shared_fixtures.py` runs both JSON files.
  `test_block_columns.py` checks the kind, `test_migrations.py` step 44 and
  a restored backup keeping `pdf_page`, and `test_share_upload_scope.py` an
  editor's fresh upload.
- The Notability tests (`test_notability.py`, `test_notability_import.py`,
  `test_notability_export.py`, `test_notability_samples.py`,
  `test_notability_ntb.py`): pictures cropped, stretched, turned, flipped,
  captioned and locked, both ways, an unlock made in Notability, a faded
  picture counted and kept by Gamma's copy, an SVG kept as it came, the
  pictures' `zIndex` in the order they stack and back with no `z`, and the
  sample's drawn as Notability drew them in its thumbnail.
  `test_goodnotes_import.py`: a GoodNotes picture as a picture block of its
  file as it came, turned or shown cropped, and a sticker as one PNG of its
  art for its largest frame.
- Browser: `pictures.mjs`, group `pictures`
  ([debugging.md](debugging.md#browser-end-to-end-suite)): the button, a
  move, the handles, a flip, a crop, Copy image, Reset, Fit, a paste, a
  drop, an SVG dropped (kept as it is, drawn through its `<img>`, stretched
  to its frame, served as a sandboxed download), Duplicate, the z-order
  (the page restacked, the notes in their order, its undo named), the
  keys, a lock, an opacity (on the page, in the card and on the
  clipboard), a stylus, a finger, a late upload, the
  notes, a read-only and an edit share, a highlighter
  across a picture on a light, a flipped and a Gamma Dark page and on dark
  paper (read from the screen's pixels, the picture's transparent half
  included) and over a faded picture (its pixels on a light page, its
  mask's opacity there and in Gamma Dark), a sheet, two people on one
  picture, and the lasso (a picture and a stroke taken, a locked picture
  left, both carried by a drag, undone and redone in one step from the
  strip and by Ctrl+Z, Pictures off; Select all by Ctrl+A and More; a tap
  on a picture, on a stroke over it and on a locked one; Copy and Paste
  of both, placed as they were, undone and redone as one step, Cut as one
  step with the notes under the picture left, and a paste on another
  paper showing the same upload). `select.mjs` runs it for changes under
  `frontend/src/markup/`, `ink/`, `notebook/`, `editor/`, `pdf/`, and
  `backend/gamma/picture.py` and `text_box.py`. `transfers.mjs` checks a Notability import's picture on
  screen and in the round trip.
- Browser: `inkZoom.mjs`, group `ink-zoom`: a picture in the zoom window,
  drawn as the page draws it and dimmed on a dark page (read from the
  screen's pixels). It also checks a highlighter multiplying into it, a
  press there going to the ink, a move shown at once, and its shade under
  a recording's player. A second picture stacks by its `z` as on the page.
- The iPad app's shared images: `openedFiles.test.mjs` (which files are a
  picture, and which workspace takes images alone) and two steps of the
  `ipad` group (`ipad.mjs`). Its stand-in shell hands two PNGs to an open
  PDF page, which land one after another, the second selected. A PNG
  handed with a PDF lands before the PDF's page opens. It also hands a
  HEIC, which Chromium cannot read, and a PNG with only a note page open,
  which the status names. A read-only workspace refuses a PNG alone in
  place. A shared one lets it go at its library with no question, and on
  a PDF page there the question offers no personal workspace for it.
  `select.mjs` runs the `ipad` group for `openedFiles.js` and
  `usePictures.js`.

## Not built yet

- **A lasso step's undo** steps its two halves together only while each
  is its history's newest entry.
  - After an edit in the notes, the strip's Undo takes back the strokes
    alone, and its pill says the pictures stay. A later Ctrl+Z takes back
    the pictures alone.
  - After a stroke drawn since, Ctrl+Z takes back the pictures alone, and
    its pill says the handwriting stays.
- An SVG in the PDFs is what MuPDF draws of it, which is not always what a
  browser draws: CSS beyond inline styles, filters, masks and web fonts may
  differ. Its text is set in MuPDF's fonts.
- WebKit was checked in Playwright's WPE build on Linux, not in Safari or
  on an iPad. There an SVG's `<img>` draws under the file's own policy as in
  Chromium: its `<style>` and `style` attributes kept, its script not run,
  nothing it names fetched. Its natural size is Chromium's (a viewBox alone
  300 × 150, one side from the viewBox's aspect, an em 16 px), so `svgFill`
  stretches it alike. Opened at its URL, that build shows the SVG instead
  of downloading it, in an opaque origin with no script run: the policy's
  `sandbox` is what holds there. Safari's download, and an SVG on an iPad,
  shared to the app or opened at its URL, wait for a device
  ([ipad.md](ipad.md#device-checks), check 38).
- In the iPad and Android apps an SVG has no time limit: one that takes
  MuPDF minutes holds This iPad's or This device's PDF work that long
  ([SVG pictures](#svg-pictures)).
- The time limit counts from a drawing's start, not the wait for one of
  the two at once. A drawing kept stays until the server restarts or newer
  ones push it out, so a file that timed out under a passing load stays out
  of the PDFs until then.
- A PDF that holds an SVG as vectors is drawn by the server itself, under
  the PyMuPDF lock: the AI's and the feedback reader's pictures of a PDF
  page with a picture on it (`ink_view.page_with_handwriting`). The drawing
  at `SVG_DRAW_SIDE` proved the SVG draws in time that large, and such a
  picture is at most `RENDER_MAX_SIDE` a side, but a crop zoomed up to 4×
  can draw the SVG larger than the proof did, with no time limit.
- An SVG in a Zotero note without the PDF copy is embedded as a data URI.
  Whether Zotero's note editor shows one is unchecked.
- A highlighter over a faded picture on a dark page or dark paper is not
  checked on screen. The e2e step reads its mask's opacity in Gamma Dark,
  not its pixels.
- The Picture button has no row in the strip's More menu and no command in
  the catalogs; `I` is a strip key only, while the strip is open.
- Not checked on an iPad: the Picture button's picker (Photos, the camera,
  Files), a HEIC photo, which Safari decodes and the page re-encodes, and a
  photo shared to the app ([ipad.md](ipad.md#device-checks), checks 31, 32
  and 34).
- A shared image that finds no surface is let go, not kept for the next
  page. So is every image shared at a cold launch, since the app starts on
  the library. One that goes to the personal workspace with other files is
  let go there, since that workspace opens on its library.
- Whether WebKit (Safari, the iPad app) applies a WebP's EXIF is unverified.
  The rule follows Chromium and Firefox, which were measured.
- Notability: how the app opens a turned, cropped, flipped, captioned or
  locked picture Gamma wrote is untested. Its own drawing of the sample puts the
  flips before the turn, as Gamma does; where its crop falls against the
  flips no sample shows. Two pictures that show one file go into a `.note`
  as two files under `Images/`.
- The server's merge does not know the surface, so it cannot keep its
  result on the page. A move to the edge merged with a larger size can stand
  a little off the page until the next edit keeps it on.
- Backspace in an empty caption row deletes the picture block, the
  outliner's rule for ink groups and boxes too. "Block deleted." undoes it.
- A finger that lands on a picture not selected, to scroll, lets go of the
  selected one.
- Nothing changes `pdf_page` when a picture's row moves in the notes, as for
  a box: one moved to another page keeps its number.
- The caption is not drawn on the page, as Notability draws it. There are no
  rounded corners, text wrap or freehand crop.
- A foreign stamp's skew is lost, and a stamp that draws more than one image
  or any text is no picture.
- The import's bounds stop at the images: an object stream, and the `/RC`
  stream `_rc_text` reads, are inflated whole, up to pypdf's own 75 MB cap
  for each.
- The annotated PDF is checked with MuPDF, not in Acrobat or macOS Preview.
