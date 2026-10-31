# Pictures on the page

A photo, a screenshot or a figure placed on a PDF page or a sheet of paper,
like a picture in Notability or GoodNotes. A picture is a block: its content
is its caption, Markdown like any note, and one property, `picture`, places
it. It is moved, scaled, stretched, turned, cropped and flipped in place, and
the stored file never changes. The reasoning and the plan are in
[research/pictures.md](../research/pictures.md).

Code: `frontend/src/markup/` (`picture.js`, `usePictures.js`,
`PictureLayer.jsx`, `pictureUpload.js`, `TransformHandles.jsx`, `placed.js`,
`markup.css`, and the parts shared with text boxes and handwriting:
`MarkupLayers.jsx`, `MarkupToolbar.jsx`, `PageTools.jsx`), `gamma/picture.py`
(the server's rules and the picture in a PDF), the `/Stamp` writer in
`gamma/pdf_export.py`, the reader in `gamma/routers/imports.py`, the images
in `gamma/pdf_image.py`, `gamma/notebook.py` `placed_ops`, and, for the
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
- **The surface in view** takes a pick and a paste: the notebook's current
  sheet, else the PDF page in view, else the sheet among the notes that
  shows the most of itself. With none, a pill says to open a PDF page or a
  notebook page first.
- **Size and place.** A new picture keeps its natural aspect, fits inside
  half the surface's width and height, is never larger than its pixels as
  points, and is centred on the drop point or the surface, kept on it.
  Several files land one after another, each 12 CSS px right of and below
  the one before.
- **Selected as it lands**, unless the selection changed while it went up
  (another picture, a box or the lasso's strokes selected, a tool armed, the
  picture let go of) or a text field has the focus. Then it and the rest of
  the drop land unselected, so a box being typed in stays open.
- **What goes up.** PNG, JPEG, GIF and WebP up to 24 MP go up as they are,
  and a GIF keeps its animation. The browser decodes anything else it can
  read (HEIC and HEIF in Safari and the iPad app; TIFF, BMP, SVG),
  and that, or anything larger, is drawn on a canvas, downscaled and sent as
  a JPEG. A format that may hold transparency goes as a PNG. The original is
  not kept.
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
Send to back and Show note, and stays open for the session.

- **Duplicate** makes a copy with the caption, not the notes under it, 12
  CSS px right and down, over the original, selected.
- **Copy image** puts a PNG of the picture as it shows on the clipboard: the
  crop at its own pixel density, flipped and turned, with clear corners.
- **Fit to page** makes the turned frame as wide as the page less 24 pt each
  side, shrunk to the page's height less 24 pt each side when needed, and
  centres it.
- **Reset** brings back the whole image, upright and unflipped, at its
  natural aspect, as wide as it was and about the same centre.
- **Bring to front** and **Send to back** move the picture's block, with the
  notes under it, after the last or before the first of the surface's
  pictures. The notes show the new order too ([Model](#model), "Order").
- **Show note** shows the block in the notes.
- **Crop mode** opens from the menu or a double click (a finger's double
  tap). The whole image shows dimmed in the picture's frame, placed,
  flipped and turned as the picture is. The part kept is bright, with a
  handle at each corner and side.
  - A handle moves its edges, at most to the image's edge and never closer
    than 1 % of the image to the edge across. A drag inside moves the crop.
  - Enter or the check applies it. The part kept stays where it was on the
    page at the same scale, and the frame shrinks or grows to it, so a
    stretch is kept.
  - Escape, the cross or a press outside cancels.
  - The crop's handles are 12 px with a 28 px hit area, on every device.

### Undo, the notes and other views

- **Undo.** While a picture is selected, Ctrl+Z and the strip's buttons step
  the block history, not the strokes. Changes to one picture alone less than
  a second apart are one step. The history names a picture's creation,
  deletion, caption edit, move, resize, rotation, flip and crop, and a run
  of several kinds is a "picture change".
- **In the notes** a picture is a row with a picture marker. Its text is the
  caption, and a card under it shows the picture as placed (cropped, flipped
  and turned), at most 320 × 240 CSS px. The marker and the card show it on
  its page and outline it; Show note goes the other way.
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
  the page ([handwriting.md](handwriting.md#client), the planes).
- **Replay.** A picture carries `t0`. Under a playing recording that covers
  it, it is shaded until its moment, and a click plays from there
  ([recording.md](recording.md)).
- **The zoom window** does not show pictures; it shows the page's pixels and
  the handwriting.

## Model

No new endpoint. A picture is a block with `picture`, plus `pdf_page` (the
1-based page) on a PDF page and `t0` from its making. A picture imported
from the PDF's own `/Stamp` also carries `imported_annot` and
`annot_stripped`, as a text box does. Migration step 44 teaches the block
table's `kind` column the new kind (below).

| `picture` key | meaning | default |
|---|---|---|
| `url` | the stored file, `/api/uploads/<stem>.<ext>`: a stem of 1 to 128 letters, digits, `-` and `_`, and `png`, `jpg`, `jpeg`, `gif` or `webp` in any case (`URL_RE`, `PICTURE_URL`); SVG is left out until it is wanted on a page | required: without it the value is no picture |
| `x`, `y` | the top-left of the frame before the turn, from −`MAX_COORD` to `MAX_COORD` | 0 |
| `w`, `h` | the frame's size: the picture as shown after the crop and before the turn, at least 8 pt (`MIN_SIDE`); an aspect other than the crop's is a stretch | 144 (`DEFAULT_SIDE`) |
| `angle` | the turn about the frame's centre in degrees, clockwise as seen (CSS `rotate`, y down), in [−180, 180) | 0 |
| `crop` | `{x, y, w, h}` as fractions of the image as the browser shows it, each side at least 0.01 (`CROP_MIN`), inside the image | the whole image, `{0, 0, 1, 1}` |
| `flip_h`, `flip_v` | mirrored about the frame's own vertical or horizontal axis | false |

`MAX_COORD` is a text box's bound, 14 400 pt. On a PDF page the frame is the
one ink files use ([handwriting.md](handwriting.md#the-stroke-file-gamma-ink-v2));
on a sheet it is points from the sheet's top-left. Everything that draws a
picture applies the crop, then the flips, then the turn: the CSS, the PDF
matrix and the Notability writer.

- **No pixel size is stored.** The crop is in fractions, so each reader
  takes the size from the file: the screen from the `<img>`, the PDF
  writers from the image XObject, the Notability writer from the header.
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

`tests/shared/picture.json` pins both sides, with the cases of
`keep_on_surface`, the turned corners and the Markdown line.
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

**Order.** Document order among a surface's pictures is their z-order, as
for text boxes. Bring to front and Send to back move the block after the
last or before the first of the surface's pictures (`orderPicture`), so the
notes show the order too. No `z` key is
stored. The server reads the order through `notebook.document_order`:
each block before its children, siblings by position. On
every surface the pictures lie under the text boxes, and both under the
ink.

**The `kind` column** gains `picture`, tested after `text_box`
([user_db.md](user_db.md)). Migration step 44, `picture_kind`, is a
workspace part only: `normalize.block_columns` finds the column's stored
definition outdated and rebuilds it, as steps 29, 30 and 38 did
([migrations.md](migrations.md)). `SCHEMA_VERSION` and `workspace_format()`
are 44. `blocks_store.block_kind` agrees with the column, and the search
panel labels a hit "picture". A backup restore runs
`normalize.highlight_shape`, which keeps a picture's `pdf_page` as it keeps
a box's.

**Merge.** Every change sends the whole `picture`. Taken as one value,
one person's crop would undo another's move. So a `set` of `picture` whose
`base_props` names the picture it was changed from merges, as a text box's
does ([text_boxes.md](text_boxes.md#model)).

- `merge_picture(stored, mine, base)` is the stored picture with the keys
  the writer changed from its base, taken from `mine`. Keys compare
  normalized, so a default spelled out is no change, and the crop compares
  by value. Where both changed a key, the later writer wins.
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
    keep it on the surface only when given one.
  - Crop mode's `imageDelta` (a screen drag as fractions of the image,
    through the turn and the flips) and `dragCrop`; the z-order's
    `orderPicture`; the drawing's `pictureLayout`.
  - `captionText` and `pictureMarkdown`, the `![caption](url)` line, which
    read the caption's Markdown as the words it shows (`captionPlain`, the
    twin of `text_box.plain_text`).
- `PictureLayer.jsx`: one surface's pictures.
  - `PictureLayer` is a size container over the surface, its pictures placed
    in its percentages, so a zoom needs no re-render. It takes the image
    drags and drops (below).
  - `PictureView` draws one picture: the frame placed and turned by
    `rotate(angle deg)`, an inner box that clips to the frame and mirrors by
    the flips, and the `<img>` sized and offset so the crop fills the frame.
    A stretch is the frame's aspect against the crop's, so `object-fit` is
    never needed. A turned element takes pointer events only inside its
    turned box, so the browser does the hit test.
  - `PagePicture` is a picture on the page: the press, the move, the keys,
    the handles' values (`handleValue`), the menu's actions and crop mode.
    It is memoized, and the page's marks slice keeps its identity while its
    pictures are the same (`useMarks`), so a page re-renders only for its
    own pictures.
  - `PictureMenu`, `PictureCrop` and `PictureCropBar` are the menu, crop
    mode and its two buttons. The menu uses the lasso menu's classes and
    placement (`useSelectionMenuAnchor`, `InkTooltips`).
  - `PictureCard` draws the picture in the notes (`editor/BlockTree.jsx`),
    in its turned frame's box.
- `usePictures.js`: App's side. It owns the selection `{id, cropping}`, the
  flash, the pictures by surface and every edit, made through `setBlocks`,
  so the op diff sends it and the history records it. A new picture is an
  undo step of its own.
  - `pick()` is the Picture button and its key: `shared/lib/pickFiles.js`
    opens the picker ([ui-design.md](ui-design.md)), and there is no file
    input in App.
  - A `paste` listener on the document takes image files when no text
    field has the focus.
  - `place(files, surface, size, at)` uploads the files one after another
    and makes each a block (`addPlaced`).
- `pictureUpload.js`: the upload and the re-encode (below), the drag tests
  (`imageFiles`, `onlyImages`, `picturesDrag`) and Copy image's `shownPng`.
- `TransformHandles.jsx`: the handles a lasso selection and a picture share
  (below).
- `placed.js`: what a text box and a picture share as objects on a surface:
  `withPlaced(tree, id, key, fn)`, one placed value's edit, and
  `addPlaced(tree, surface, id, properties)`, where a new one goes.
- `MarkupLayers.jsx` mounts the picture layer, `MarkupToolbar.jsx` has the
  button (`onPicture`), and `PageTools.jsx` carries the `onPicture…`
  actions among the page's actions.
- `ink/stripKeys.js` maps `I` to `{kind: "picture"}`, and
  `ink/useStripKeys.js` calls `onPicture`.
- `app/App.jsx`:
  - `pictureTarget()` is the surface a pick or a paste lands on: `pasteTarget()`
    (`ink/useInkSurfaces.js`), else the sheet among the notes that shows the
    most of itself (`visiblePart`).
  - `boxUndo` and `hasBox` cover a selected picture: the strip's buttons and
    Ctrl+Z go to the block history, and Escape to the picture.
  - `showOnPage(id)` scrolls to a picture's turned box and flashes it.
  - The app's drop overlay stays off over a surface (`picturesDrag`).
- `editor/BlockTree.jsx`: a picture's row draws its marker
  (`.pictureMarker`, beside ink's and a box's) and `PictureCard`. `onSheet`
  goes down the recursion, so a picture under a sheet shows no `p.N`. A
  row's dragover leaves its outline off for images over the sheet it draws.
  Copy as markdown writes `pictureMarkdown` for a picture.
- `editor/blockHistory.js`: `describeTransition` names a picture's making,
  deletion and caption edit, and `pictureChange` a change by the keys it
  changed. `observeTree` folds changes of one picture's `picture` alone
  into one step within `EDIT_MERGE_MS` (`boxChange`, which kept its name).
  `rebaseHistory` carries another client's picture onto each entry through
  `PLACED`.
- `shared/model/blockModel.js`: `isHighlightBlock` refuses a picture, and
  `blockPage` reads its `pdf_page`. It tests for one itself
  (`placesPicture`): importing `picture.js` would be a cycle.
- `shared/lib/copyImage.js` `copyPng` makes Copy image's `ClipboardItem` in
  the click, so Safari keeps the gesture while the PNG is drawn.
- `markup.css`: the layer, the frame, the outline, the flash, crop mode and
  the card. `app.css`: the marker.

**The selection rules, shared with text boxes and the lasso:**

- Selecting a picture makes App let go of a box (`onBoxDeselect`) and of the
  lasso's strokes (`clearInkSelection`). Selecting a box or strokes calls
  `pictures.deselect()`.
- Every change of the selection moves a counter in `usePictures`: a
  picture selected or let go, a tool armed, and `deselect()`, which App
  calls when a box or strokes are selected. An upload that lands takes the
  selection only when the counter is where it was as its upload began and
  no text field has the focus. Once one file lands unselected, the rest of
  its drop does too.
- A tool armed, another page opened, or the selected picture no longer
  drawn (undone, deleted, moved off its surface, by anyone) lets go of it.
- `markup/TextBoxLayer.jsx`: with the Text tool armed, its capture-phase
  claim skips `.pagePicture.selected, .pictureHandle, .pictureCrop`.
- `ink/InkLayer.jsx` `NOT_INK` gains `.pagePicture.selected` and
  `.pictureCrop`, so a press there starts no ink, from a stylus that writes
  at once either. The handles are `.inkTransformHandle` and so were in it
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
- `pct` and `arrowSign` live here; the ink layer imports them.

**The upload and the re-encode** (`pictureUpload.js`):

- `encodePlan(type, w, h)`: a PNG, JPEG, GIF or WebP of at most `MAX_PIXELS`
  (24 000 000) is kept. Anything else is drawn at a size within
  `MAX_PIXELS` and `canvasSize`'s caps, as a JPEG (quality 0.9), or a PNG
  when it came as PNG, WebP, GIF or SVG.
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
  (5). The zoom window's copy of a surface (`chrome` false) has none.
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
- The dark-page filter in `app.css` inverts ink and `.textBoxBody` only;
  `markup.css` dims a picture's image to `brightness(0.88)`.
- `MarkupLayers` passes the surface's pictures to its ink layer (the zoom
  window's copy gets none). On a surface with pictures each highlighter
  plane is drawn through a mask of the surface less its pictures, and
  again, a `<use>` of its strokes, through an alpha mask of the pictures'
  images in `svg.inkOverPictures`, which always multiplies and which
  neither the dark-page filter nor a `[data-blend]` rule reaches.
  `ink/overPictures.js` `pictureShape` places each image there as
  `PictureView` does: the crop, then the flips, then the turn. The masks
  follow the pictures as stored, so while a picture is dragged or its
  handles size or turn it they keep its place until the change lands. The
  stroke in progress and peers' wet highlighters keep the page's blend
  over a picture until they land.

## Server and interchange

- `gamma/picture.py`: the rules above, and the picture in a PDF.
  - `normalize_picture`, `is_picture`, `picture_page`, `merge_picture`,
    `resend_picture` (the mirror's resend, above), `turned_corners`,
    `turned_box`, `turned_size`, `keep_on_surface`.
  - `pdf_ops(pic, name)` draws the image XObject `name` where the screen
    shows it, in a y-down frame of the surface: from the frame's centre,
    turned, mirrored about the frame's axes, clipped to the frame, and the
    whole image scaled so its crop fills the frame. It takes no pixel size:
    the crop is in fractions and an image fills the unit square.
  - `caption_text` is the caption on one line. `markdown_of(content, pic)`
    is the `![caption](url)` line the text exports and the AI print: the
    caption read as text (`text_box.plain_text`), on one line, without
    brackets and pipes, escaped once (`escape_markdown`). So an imported
    caption that came escaped (`my\_photo.jpg`) is not escaped twice, and
    `**Bold** figure` reads `Bold figure`.
  - `image_size(data)` is a file's pixel size as the browser shows it, read
    from the header by the format's own Pillow plugin. Nothing is decoded,
    so a file of any declared size answers at once.

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
`_collect_text_boxes` and `_on_sheets`, walks the page in document order and
keeps each picture that normalizes, is on a PDF page and has its file.

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
  Pictures and boxes go in document order, their z-order.
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
  (`_upright`, a box's), so it stands as the screen shows it.
- The image XObject comes from `pdf_image.XObjectStore`, so a JPEG and a
  plain PNG go in verbatim and the rest is decoded through Pillow, its
  transparency a soft mask.
- `/Contents`: the caption's plain text on one line, references read as
  their labels (`resolve_ref`), as a box's. An empty caption writes none.
- `/F 4`: print. `/NM` from the block id, `/T` the author, `/M` the block's
  `updated_at`.
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
  forms walked, name exactly one image and no font. A rubber stamp of words
  or vectors makes nothing, as before.
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
  needs. PyPDF2's `get_data` would inflate it whole and cache the result.
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
- A stamp whose image does not read or is past the budget makes no
  block, with a log line, and stays in the file: the strip keeps it
  (`_strip_embedded_annotations(keep=…)`), for a later import to try
  again. Storing a picture meets one refusal, the storage quota
  (`_picture_url`): a full workspace (507) stops the import, nothing comes
  in and nothing is stripped. Replies go only under records that made a
  block.

**Notebook PDF** (`notebook.notebook_pdf`, [notebooks.md](notebooks.md)):
each sheet's pictures (`sheet_pictures`) are page content after the paper,
then its text boxes over them, then the ink. `notebook.placed_ops` draws the
pictures and the boxes, and `pdf_export.page_with_ink` uses it too. A
picture whose file cannot be read is left out.

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
  (`_placed_as`: the crop as the part kept, the turn, the flips).
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
  its turned frame fitted to the column (never enlarged, at most a page
  tall), then a small grey "picture, p. N" line, then the caption. One that
  cannot be drawn is still named.
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

## The Notability import and export

[import_export.md](import_export.md#notability-notes) has the rules, and
[research/notability-format.md](../research/notability-format.md) the
format.

- **Import.** A picture becomes a picture block: on a PDF page with its
  `pdf_page`, on paper under its sheet (`notability_map.picture_block`). A
  PNG, JPEG, GIF or WebP is stored as it came and never decoded; only its
  header's size is read (`picture.image_size`), to turn the crop into
  fractions. A TIFF, BMP or AVIF is stored as a PNG of its first frame,
  turned as its EXIF orientation says (`note_app_import.picture_file`), up
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
- The GoodNotes import's pictures become picture blocks through the same
  storing (`note_app_import.AppImport.store_picture`): the frame (`#2`)
  through the page's and the turn (`#3.#3`, in radians) as `angle`, the
  file as it came (a TIFF, BMP or AVIF as a PNG), a die-cut sticker's PDF
  rendered to a PNG first, once for all the pictures that show it
  (`goodnotes_import._sticker`). A GoodNotes crop is not applied yet: the
  picture comes in whole, counted
  ([import_export.md](import_export.md#goodnotes-notebooks)).
- Each file is read and stored once per note, however many pictures show
  it. `copied_picture` takes Gamma's own copy of a picture back when the
  note still shows it as the copy has it.
- **Export.** A picture block becomes an `ImageMediaObject` with
  `rotationDegrees` from the turn, `FigureCropRectKey` the crop in the file's
  pixels and, for a cropped picture, the same rectangle as the path
  Notability keeps beside it (`$0`, `notability_write.crop_path`), the
  flips, and the caption's plain text as `captionFieldText`. A surface's
  pictures go under its boxes, and both under its stickies, as on screen.
- Pictures imported before pictures were blocks stay text boxes holding an
  image. There is no migration: their pixels were transformed and the crop
  is gone. The export still writes such a box as a picture, which comes back
  a picture block.

## Tests

- `frontend/tests/picture.test.mjs`: the shared cases (`picture.json`'s
  normalize, keep, turned and markdown, and `picturemerge.json`), the
  constants against the server's, `isPicture`, `picturesBySurface` (a PDF
  page's in document order, a sheet's those no nearer sheet holds), and the
  geometry: `placePicture`, `movePicture`, `scalePicture`, `stretchPicture`,
  `turnPicture`, `cropTo`, `fitPicture`, `resetPicture`, `pictureLayout`.
- `frontend/tests/pictureEdits.test.mjs`: crop mode's geometry, the z-order,
  the line a row copies as, what a file becomes before it goes up and what
  its upload answers, and which drags a surface takes.
- `frontend/tests/placed.test.mjs`: `withPlaced` and `addPlaced`.
- `frontend/tests/overPictures.test.mjs`: where the highlighter's mask puts
  a picture, its crop's corners on `turnedCorners` as the flips mirror
  them.
- `blockHistory.test.mjs` (the labels, a run as one step, the key-by-key
  rebase), `blockOps.test.mjs` (the base a change names, the page and no
  highlight), `collabSession.test.mjs` (a remote picture meeting ours on the
  way, the stored picture brought back) ([collab.md](collab.md#testing)).
- `stripKeys.test.mjs` (`I`), `designTokens.test.mjs` (no coarse-pointer
  size rule but the text box handle's), `transferFormats.test.mjs` (the
  export summary and the import's text).
- `backend/tests/test_picture.py`: what a picture block is and its page, the
  caption and its line, and `image_size` from the header alone (EXIF as the
  browsers apply it, nothing decoded, a huge declared size answered at
  once).
- `backend/tests/test_picture_merge.py`: the merge through `/ops` and `PUT
  /blocks/{id}`, a writer with no base, removal, a box and a picture in one
  batch, the mirror's diff and its resend of a lost push.
- `backend/tests/test_picture_pdf.py`: the `/Stamp` keys and place on turned
  and cropped pages with MuPDF pixel checks, highlights under pictures and
  document order, the re-import and edits in another viewer, foreign stamps
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
  `test_notability_export.py`, `test_notability_samples.py`): pictures
  cropped, stretched, turned, flipped and captioned, both ways, and the
  sample's drawn as Notability drew them in its thumbnail.
  `test_goodnotes_import.py`: a GoodNotes picture as a picture block of its
  file as it came, turned or shown cropped, and a sticker as one PNG of its
  art for its largest frame.
- Browser: `pictures.mjs`, group `pictures`
  ([debugging.md](debugging.md#browser-end-to-end-suite)): the button, a
  move, the handles, a flip, a crop, Copy image, Reset, Fit, a paste, a
  drop, Duplicate, the z-order, the keys, a stylus, a finger, a late upload,
  the notes, a read-only and an edit share, a highlighter across a picture
  on a light, a flipped and a Gamma Dark page and on dark paper (read from
  the screen's pixels, the picture's transparent half included), a sheet,
  and two people on one picture. `select.mjs` runs it for changes under
  `frontend/src/markup/`, `ink/`, `notebook/`, `editor/`, `pdf/`, and
  `backend/gamma/picture.py` and `text_box.py`. `transfers.mjs` checks a Notability import's picture on
  screen and in the round trip.

## Not built yet

- **Phase 4 of the plan**: the lasso taking pictures with strokes; lock; a
  stored `z` key; an opacity key; SVG on the page; pictures in the zoom
  window; a photo shared to Gamma from the iPad's share sheet becoming a
  picture.
- The Picture button has no row in the strip's More menu and no command in
  the catalogs; `I` is a strip key only, while the strip is open.
- Not checked on an iPad: the Picture button's picker (Photos, the camera,
  Files) and a HEIC photo, which Safari decodes and the page re-encodes.
  [ipad.md](ipad.md#device-checks) lists the file picker in general.
- Whether WebKit (Safari, the iPad app) applies a WebP's EXIF is unverified.
  The rule follows Chromium and Firefox, which were measured.
- Notability: how the app opens a turned, cropped, flipped or captioned
  picture Gamma wrote is untested. Its own drawing of the sample puts the
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
- The import's bounds stop at the images: PyPDF2 still inflates object
  streams without a bound, and `_rc_text` an `/RC` stream.
- The annotated PDF is checked with MuPDF, not in Acrobat or macOS Preview.
