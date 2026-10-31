# Pictures on the page: a native picture object, and Notability's pictures imported as one

Plan, 2026-10, built that month (phases 1 to 3): the mechanics are in
[dev/pictures.md](../dev/pictures.md), and where the build differs from this
plan is listed in "As built" at its end. The sections "What exists" and
"What Notability stores" are findings from the code before the build and
from the earlier research notes, the rest is the design and its phases.

## The question

A picture (a photo, a screenshot, a figure clipped from another paper) placed
on a PDF page or a sheet of paper the way handwriting and text boxes are:
an object on the page, moved, scaled, turned, cropped and flipped in place,
not an image inside a note's text. And Notability's pictures brought in as
that object, with their crop, turn and flips kept as data rather than baked
into the pixels, so a note imported from Notability is still editable and
goes back out as it came.

## What exists

**Pictures in notes.** A note embeds a picture as Markdown,
`![alt](/api/uploads/<hash>.png)`, sized Obsidian-style `![alt|300](…)`.
`POST /api/upload-image` stores PNG, JPEG, GIF, WebP and SVG by content
hash (`storage.store_file`, `ALLOWED_IMAGE_TYPES`); the editor uploads a
paste, a drop and `/image` through it (`editor/BlockTree.jsx`
`uploadImageFile`), and `editor/MdTools.jsx` `MdImage` draws it with a
width grip, a caption and a zoom. Nothing places a picture on a page.

**Objects on a page.** Two kinds exist, both blocks of the page
([dev/text_boxes.md](../dev/text_boxes.md), [dev/handwriting.md](../dev/handwriting.md)):

- A **text box** is a block with `text_box: {x, y, w, h, auto, size, color,
  bg}` in points of its surface and `pdf_page` on a PDF page; under a sheet
  it is on the nearest sheet above it. Its content is its text. Both sides
  normalize it (`gamma/text_box.py`, `markup/textBox.js`, pinned by
  `tests/shared/textbox.json`), a `set` is merged key by key when the op
  names its base (`ops.py`, `sync_engine._box_again`,
  `collabSession.js`, `blockOps.js`, `blockHistory.js` rebase), the
  generated `kind` column names it (`db.BLOCK_HOT_COLUMNS`,
  `blocks_store.block_kind`), `highlights.is_highlight` refuses it, the AI
  labels it (`ai_context.text_box_label`), and every export writes it (a
  `/FreeText` in the annotated PDF, page content in the notebook PDF, a
  note with its page in the text exports).
- An **ink group** is a block with `ink_url` naming a `gamma-ink` v2 file
  and `pdf_position`; its content is a caption. Its file is an upload like
  a pasted image, which is why orphan cleanup, share-scoped serving, quota
  and the export bundlers already understand `/api/uploads/` references in
  properties: `storage.upload_refs` scans the properties JSON for them,
  `routers/uploads._pages_reference` tests `instr(properties, …)`, and
  `upload_gc.referenced` reads the same set.

**The layers** (`markup/MarkupLayers.jsx`): on every surface, the text-box
layer first and the ink layer over it, both at z-index 6, above a PDF
page's highlights, note badges and link boxes. The three places that show a
surface (a PDF page, a sheet in the notebook view, a sheet among the notes)
all mount `MarkupLayers`, so a new layer reaches all three at once. The
ink layer's `NOT_INK` selector lists what a press must not start a stroke
on (a box's handle and band, the menus, the ruler); `ABOVE_PASSIVE_INK`
what a read-only tap prefers over a stroke.

**Selection and transforms.** The lasso selection has a bottom-right resize
handle and a top-right rotate handle (`InkLayer.jsx` `InkTransformHandles`,
not exported): both act about the selection's centre, resize keeps
proportions and scales the stroke width, Shift snaps the turn to 15°, a
focused handle takes arrow keys, the handles keep their screen size and
follow the live preview, and `transformStrokes` writes the result. A text
box has only a width handle and cannot turn ("a box cannot turn" is a rule
the Notability import works around). One selection at a time in the app: a
box selected lets go of the lasso's and the reverse.

**The frames.** On a PDF page, points of the pdf.js viewport at scale 1,
rotation applied, origin top-left, y down: the frame ink files and text
boxes share, which `pdf_export.viewer_point_to_pdf` maps to user space
with the page's rotation and view box. On a sheet, points from its
top-left.

**The Notability import of pictures today**
([dev/import_export.md](../dev/import_export.md#notability-notes),
`notability_import._store_picture`, `notability_map.picture_box`,
`notability_import.picture_bytes`): a picture becomes a fixed-width text
box holding `![caption](/api/uploads/…)`, the box the picture's turned
bounding box grown by the box padding. Its bytes are stored as they came
only when nothing is to be done to them; a cropped, flipped, turned or
stretched picture goes through Pillow (EXIF orientation, crop, flips,
stretch, turn, in that order) and a turned one comes out as a PNG with
transparent corners, its longer side at most 2,048 px. An animated GIF
that is cropped, flipped or turned becomes its still frame. The Notability
export (`notability_export._picture`) writes such a box back as an
`ImageMediaObject` with the whole image as its crop and no turn, so a
turned picture round-trips as a pre-turned PNG in an upright frame. The
loss is exactly what a native object would keep: the original pixels, the
crop and the turn as numbers, the animation.

## What Notability stores, and does

From [notability-format.md](notability-format.md) "Pictures" and
[ink-storage.md](ink-storage.md):

- `.note`: an `ImageMediaObject` (or `GifMediaObject`) with
  `documentOrigin` (the top-left before the turn), `unscaledContentSize`
  (the shown size after the crop and before the turn, so a stretched
  picture has an aspect other than its crop's), `rotationDegrees` (radians,
  about the centre, clockwise as seen), `isFlippedHorizontal` /
  `isFlippedVertical`, `zIndex`, a `figure` whose `FigureCropRectKey` is the
  crop in source pixels (the whole image when uncropped) and whose snapshot
  names the file under `Images/`; `isGif` and `assetsIdKey` name an
  animated GIF's `Assets/<id>/media.gif` beside its still frame; a caption
  in `captionFieldText` when `isCaptionEnabled`. The order of the flips and
  the turn is not documented: the import takes flip, then turn.
- `.ntb`: a media record (kind 22) with the page, the frame's top-left as
  turned about its centre, the turn, the size, the asset by SHA-512 with its
  MIME type and logical size, and a crop in that logical size
  (`notability_ntb._media`). No flip field is known.
- Gamma's reader already yields all of this as `notability.Media(kind=
  "picture", x, y, w, h, rotation, flip_h, flip_v, z, picture, animation,
  crop, crop_size, caption, uuid)`: the import below is a change of what
  the mapper makes of it, not of the parser.
- Notability's own UI
  ([Add and Edit Media](https://support.gingerlabs.com/hc/en-us/articles/205270648-Add-and-Edit-Media)):
  photos, the camera, stickers, GIFs and scans; a tapped image shows a menu
  with Crop, Duplicate, Cut, Copy, Lock, Send Forward / Backward and
  Delete, and an options menu with Format (caption, rounded corners, text
  wrap), Fit to Page, Flip Vertically / Horizontally, Bring to Front / Send
  to Back and Convert to Ink; a rotation handle on the right snaps at 90°,
  the caption staying horizontal. Ink drawn over an image moves with it
  unless the image is sent backward. GoodNotes
  ([Image Tool](https://support.goodnotes.com/hc/en-us/articles/6445991371663-Insert-images-with-the-Image-Tool),
  [Cropping](https://support.goodnotes.com/hc/en-us/articles/202167535-How-to-crop-an-image-))
  resizes by a corner handle, crops by rectangle or freehand outline, and
  lets the lasso scale and turn an image with the ink.

## The decision: a block with a `picture` property

A picture is a block of the page with one property, `picture`, that
places it, as `text_box` places a text box. Its content is its caption,
as an ink group's is. It is drawn on its own layer under the text boxes
and the ink.

Three shapes were weighed:

1. **An element inside the `gamma-ink` file**, so the lasso, the eraser and
   the replay treat it as ink. Declined. The file's `space.kind` is a
   closed literal ("a third kind would be a version 3"), a stroke must have
   `pts`, and every consumer (hit test, eraser, exporters, the merge by
   stroke id, the iPad's live line, `view_ink`) would need to learn a
   non-stroke element. The file is content-addressed and capped at 6 MB,
   while a picture is an upload of its own anyway. What is wanted of this
   shape, a lasso that takes pictures along with strokes, is a selection
   rule (phase 4), not a storage one.
2. **A text box whose content is one Markdown image**, which is what the
   Notability import makes now. Declined as the model: the crop, the turn
   and the flips have no place in Markdown, the box's own keys (`size`,
   `color`, `auto`) mean nothing for a picture, and the box cannot turn.
   It stays as what old imports made, readable and exportable as before.
3. **A block with `picture`** (chosen). Everything that makes a text box
   work on the page is a pattern to copy with a second key: the
   normalizers pinned by a shared fixture, the nearest-sheet rule, the
   key-by-key merge, the `kind` column, the layer, the selection, the
   history labels. The URL in the property is found by every place that
   already scans properties for uploads, so storage, sharing, cleanup,
   backups and the Gamma export need nothing new.

Content as the caption rather than as `![caption](url)`: one source of
truth for the file (a URL both in the property and in the text would have
to be kept in step), and a caption a row can be typed into without breaking
the picture. The text exports and the AI write the Markdown form from the
property in one helper each; that is the whole cost.

## The model

| `picture` key | meaning | default |
|---|---|---|
| `url` | the stored file, `/api/uploads/<hash>.<ext>`, PNG, JPEG, GIF or WebP (`storage.UPLOAD_REF_RE`); SVG is left out until it is wanted on the page | required; no `url`, no picture |
| `x`, `y` | the top-left corner of the frame before the turn, in points of the surface | 0 |
| `w`, `h` | the frame's size: the picture as shown after the crop and before the turn (Notability's `unscaledContentSize`), at least 8 pt a side; an aspect other than the crop's is a stretch | required |
| `angle` | the turn about the frame's centre, degrees clockwise as seen on the screen (CSS `rotate`, y down; Notability's `rotationDegrees` converted from radians; the sign `transformStrokes` turns by), normalised to [−180, 180), hundredths | 0 |
| `crop` | `{x, y, w, h}` as fractions of the image as the browser shows it (EXIF orientation applied), `w` and `h` at least 0.01, `x + w ≤ 1`, `y + h ≤ 1` | the whole image |
| `flip_h`, `flip_v` | mirrored about its own axes, after the crop and before the turn | false |

- Every value at most `MAX_COORD`, 14,400 pt, like a text box's. Every
  reader goes through `normalize_picture` (`gamma/picture.py`) or
  `normalizePicture` (`markup/picture.js`): a bad key takes its default,
  numbers clamped and rounded to hundredths as `Math.round` rounds,
  unknown keys dropped, a `url` that is no upload reference makes the value
  no picture. `tests/shared/picture.json` pins both halves, as
  `textbox.json` does.
- The order crop → flips → turn is the import's today and the one
  Notability's export suggests; it is what the CSS, the PDF matrix and the
  Notability writer all apply.
- **Placement** is the text box's rule word for word: `pdf_page` on a PDF
  page, made at the page's top level; under a sheet, the sheet's last
  child, on the nearest sheet above it whatever `pdf_page` says; neither,
  and it is a note with a picture card. `picturesBySurface(tree)` beside
  `textBoxesBySurface`; `picture.picture_page(props, on_sheet)` beside
  `box_page`.
- **No stored pixel size.** The screen reads it from the image, the PDF
  writers from the XObject, the Notability writer from the file. The crop
  as fractions is what makes that possible.
- **`t0`** at creation, as a text box carries it, so a picture added
  during a recording is shaded until its moment in the replay
  ([dev/recording.md](../dev/recording.md)).
- **Order.** Document order among the surface's pictures is the z-order,
  as it is for text boxes and as the Notability import already sorts media
  by `zIndex` into document order. Bring to front / Send to back move the
  block among its siblings (and so in the notes); a separate `z` key is
  declined until that bothers someone.

**Merge.** A `set` of `picture` whose `base_props` names the picture it
was changed from merges key by key, exactly as `text_box` does, so one
person's crop survives another's move. Rather than a second copy of the
text-box code in six places, the plan generalises it once: a tuple
`PLACED_KEYS = ("text_box", "picture")` with a normalizer per key, used by
`ops.py`, `sync_engine._box_again`, `sync_tree.py`, `collabSession.js`,
`blockOps.js` and `blockHistory.js` (`rebaseHistory`, the run merging of
one object's changes, `describeTransition`). `tests/shared/picturemerge.json`
pins the merge.

**The `kind` column** gains `picture` (a `picture` object in the
properties, after `text_box`): a migration step that redefines the
generated column and runs `normalize.block_columns` again, as steps 29, 30
and 38 did ([dev/migrations.md](../dev/migrations.md)); `blocks_store.block_kind`
and `tests/test_block_columns.py` agree with it. `highlights.is_highlight`
and `blockModel.isHighlightBlock` refuse a picture as they refuse a box;
`blockPage` reads its `pdf_page`.

## What the user sees

**Adding a picture.** Four ways, all landing on the surface in view
(`useInkSurfaces.pasteTarget`: the notebook's current sheet, else the PDF
page in view) at natural aspect, fitted inside half the surface's width
and height, centred, selected:

- **Picture** on the markup strip, after the Text tool (`P` presses it):
  a tap opens the file picker (`<input type=file accept="image/*">`, which
  on an iPad is Photos, the camera and Files with no native code), and the
  picked picture is placed at once, centred on the surface in view and
  selected, to be dragged where it goes. It is a button, not a tool that
  stays armed: the pen keeps what it was. Several files picked at once
  are placed one after another, each 12 screen px aside.
- **Paste** (Ctrl+V / ⌘V) with an image on the clipboard while no text
  field has the focus and a page or sheet is in view: the image is uploaded
  and placed at the centre of the surface in view. The handwriting
  clipboard still wins when it holds strokes (`inkCommands.js`), and a
  paste into a block editor stays the editor's.
- **Drop** an image file on a page or a sheet: at the drop point. The
  window-level drop that adds files to the library (`App.jsx`) yields to a
  drop on a surface, by a capture-phase handler on the page wrapper.
- From **Notability**, by the import (below).

A file the server does not take as it is (HEIC and HEIF from Files on an
iPad or a Mac, TIFF, BMP, SVG) is decoded by the browser
(`createImageBitmap`, the EXIF orientation applied) and re-encoded on a
canvas as JPEG, or PNG when it came as a PNG-like format with
transparency, before the upload; Safari decodes HEIC, so the iPad and the
Mac take it. A browser that cannot decode it (Chrome and Firefox with a
HEIC) gets a pill saying to convert it to JPEG or PNG first. A photo over
24 MP is downscaled the same way, so a page of camera photos stays light;
the original is not kept. The server gains no dependency: `pillow-heif` was
weighed and declined because the iPad's bundled server has no iOS wheel
for it, and the browser covers the same files on every client.

**Selecting, moving, keys.** A click selects a picture with the hand armed
or the strip closed, over a link too; a drag over 6 CSS px moves it (a
finger moves only the selected picture, as with boxes); arrows nudge 1 pt,
Shift 10; Delete deletes; Escape lets go; a second click opens nothing (a
picture has no editor) but the caption is edited in its row. At most one
picture or text box is selected in the app, never beside a lasso selection.
Read-only, a click shows its note. In the notes the block is a row with a
picture marker, a card showing the picture as placed (cropped, turned),
and the caption under it, like an ink card; the marker shows it on its page
and a click on the card jumps there, with the flash (`showOnPage`).

**Handles.** The selection frame is the turned frame (its outline turns
with the picture; CSS does the hit test). The lasso's two handles are
reused by lifting `InkTransformHandles` into `markup/TransformHandles.jsx`
with the same look and keys (`.inkTransformHandle` styles stay):

- the bottom-right **resize** handle scales about the centre, aspect kept,
  arrow keys ×1.1, at least 8 pt a side and at most the surface;
- the top-right **rotate** handle turns about the centre, Shift snapping to
  15° as the lasso does, and a magnetic snap within 3° of 0, 90, 180 and
  270 as Notability snaps, with the iPad's `snap` haptic;
- **Alt + resize** stretches (changes `w` and `h` freely), since Notability
  pictures can be stretched and the import keeps them so; without Alt a
  stretched picture keeps its aspect as it is.

**The menu** (the text box's menu classes and placement,
`useSelectionMenuAnchor`): **Crop**, **Flip** (horizontal, vertical),
**Fit to page** (as wide as the surface less a margin, centred), **Reset**
(the whole image, unflipped, upright, at its natural aspect), **Bring to
front** / **Send to back**, **Duplicate** (12 screen px aside), **Copy
image** (`shared/lib/copyImage.js`: a PNG of the picture as shown, with
its crop, flips and turn), **Show note**, **Delete**. Right-click opens it
too.

**Crop mode.** From the menu or a double click: the whole image is shown
dimmed in the picture's own frame space (turned and flipped as the picture
is), the kept rectangle bright with a handle at each corner and side; a
drag of a handle changes the crop, a drag inside moves it, never past the
image's edges. The kept part stays exactly where it was on the page, at
the same scale: the frame shrinks or grows to it (`cropTo`), as a crop in
Keynote or Word behaves, and a stretched picture stays stretched. Enter or
the check commits one history entry, Escape or a press outside cancels. A freehand crop is declined: the model is a
rectangle, and the PDF writers would need a clip path per picture.

**Undo.** While the Picture tool is armed or a picture is selected, Ctrl+Z
and the strip's buttons step the block history (the text box's `boxUndo`
rule extended); changes to one picture less than a second apart are one
step; the history names "picture", "picture move", "picture crop", "picture
rotation", "picture resize", "picture flip".

**Not in the zoom window** (it shows the page's pixels and handwriting
only, as it leaves out text boxes); **not inverted on dark pages**: the
dark-page filter that inverts ink and text boxes (`app.css`) must leave a
photo alone, so the picture layer takes a brightness reduction only.

## Drawing it

`markup/PictureLayer.jsx`, under `TextBoxLayer` in `MarkupLayers`, a size
container whose `--tb-pt` is one point, so a zoom needs no re-render. One
picture is:

```
<div class="pagePicture" style="left: x%; top: y%; width: w%; height: h%;
     transform: rotate(angle deg)">            ← overflow hidden, the hit target
  <img src=url style="width: calc(100% / crop.w); height: calc(100% / crop.h);
       left: calc(-crop.x × 100% / crop.w); top: …;
       transform: scale(flip_h ? -1 : 1, flip_v ? -1 : 1)"
       loading="lazy" decoding="async" draggable="false">
</div>
```

The stretch is the frame's aspect against the crop's, so `object-fit` is
never needed. A rotated element receives pointer events only inside its
turned box, so no hit-test code is written. The marks slice
(`useMarks`) gains `pictures`, `pictureSelection`, `pictureFlash`, kept
by identity as boxes are, so a memoized page re-renders only for its own
pictures. The ink layer's `NOT_INK` gains the picture's handles and its
menu; `ABOVE_PASSIVE_INK` gains `.pagePicture`.

## The server

`gamma/picture.py`: `normalize_picture`, `is_picture`, `picture_page`,
`keep_on_surface`, `merge_picture`, `markdown_of(block)` (the
`![caption](url)` line the text exports and the AI print), and the PDF
side: `pdf_ops(picture, xobject_name, px_w, px_h)` returning content
operators that draw the image XObject through the crop and the turn in a
y-down frame, which both PDF writers use.

- **Annotated PDF** (`pdf_export.annotate_pdf`): a `/Stamp` annotation per
  picture, written before the `/FreeText` boxes (so under them and the
  ink): `/Rect` the turned frame's bounding box through
  `viewer_point_to_pdf`, `/AP /N` a form XObject that clips to the crop and
  draws the image XObject from `pdf_image.image_xobject` (JPEG and plain
  PNG verbatim, the rest through Pillow with a soft mask, as the notes PDF
  embeds pictures), its `/Matrix` carrying the turn and the page's
  rotation as `_upright` does; `/Contents` the caption; `/F 4`; `/NM`
  from the block id; `/GammaPicture` `{"v":1, "picture":…, "caption":…}`
  for a lossless re-import. Zotero renders a stamp's appearance but does
  not import it as an annotation
  ([forum](https://forums.zotero.org/discussion/131734/feature-request-support-for-external-image-annotations-in-pdf-reader)):
  a picture is seen in Zotero, not kept by it, and the export says so once.
  `routers/export._collect_text_boxes` grows a sibling for pictures; an
  imported picture still embedded in the file is `replaced` as a box is.
- **Notebook PDF** (`notebook.notebook_pdf`): a sheet's pictures are page
  content before its text boxes, through an `XObjectStore`; `ink_view` and
  the AI's `view_ink` picture of a sheet get them for free.
- **PDF pages for the AI** (`ink_view.page_with_handwriting`,
  `pdf_export.page_with_ink`): pictures and text boxes drawn under the ink
  (today only the ink is stamped onto the page), so "the page as the user
  sees it" is that.
- **Import of embedded annotations** (`routers/imports.py`): `/Stamp`
  joins `_IMPORT_TYPES`. One with `/GammaPicture` is a picture block from
  its record, placed by its `/Rect` through `pdf_point_to_viewer` so a move
  in another viewer holds, its image re-stored from the appearance's
  XObject (a `/DCTDecode` stream is a JPEG as it is; a Flate image is
  re-encoded as PNG by Pillow, its `/SMask` as alpha). A foreign stamp
  whose appearance draws one image (Acrobat's and PDF-XChange's photo
  stamps) is a picture at its `/Rect`, unturned, whole; a stamp that draws
  anything else (a "Draft" rubber stamp) is skipped, as it was.
- **Text exports.** Markdown and Obsidian write `picture.markdown_of`,
  then the page line a text box gets; the Zotero RDF export's PDF copy
  carries the stamp, and a picture is a note with its line when there is no
  copy; the notes PDF draws it at the note's width with a small grey
  "picture, p. N" line, as it labels handwriting; Logseq writes nothing
  (not a highlight). The Gamma export and the backups carry the file by its
  property reference already.
- **AI.** `ai_context.picture_label` ("(picture on p. N)", "(picture on a
  page of paper)") beside `text_box_label`; `view_image` reads a picture
  block's `picture.url` as it reads a note's `![](…)` lines; `move_block`
  refuses to move a picture to another page unless its sheet moves too
  (`_loose_text_box` generalised).
- **Search** indexes the caption as any content; the block table's `kind`
  gives the search panel its "picture" label.

## The Notability import, natively

`notability_map.picture_block(media, frame, pixels)` replaces
`picture_box`, and `note_app_import.AppImport.store_picture` stores the
file as it came:

| Notability | `picture` |
|---|---|
| `documentOrigin`, `unscaledContentSize`, through the page's frame (`_surface_rect`) | `x`, `y`, `w`, `h`, kept on the surface |
| `rotationDegrees` (radians) | `angle` in degrees |
| `FigureCropRectKey` in source pixels (an `.ntb`'s in its logical size, `crop_size`) | `crop` as fractions of the oriented image's size |
| `isFlippedHorizontal`, `isFlippedVertical` | `flip_h`, `flip_v` |
| the image file; an animated GIF's `Assets/<id>/media.gif` | `url`: the GIF itself when the note has it, else the still frame; the browser animates it cropped, flipped or turned alike |
| `captionFieldText` | the content |
| `zIndex` | document order among the page's objects, as today |

- The bytes are stored as they came (`store_file`, quota, dedup),
  never decoded to be transformed: no 2,048 px cap, no PNG of a turned JPEG,
  no still frame of a turned GIF. The one thing read is the image's size,
  from its header (Pillow opens lazily; `pdf_image._png` and `_jpeg` read it
  without Pillow), to turn the crop into fractions; a file whose size cannot
  be read keeps the whole image and the note's warning says so.
  `MAX_PICTURE_USES`, `MAX_PICTURE_PIXELS` and `picture_bytes` go with the
  decoding: the writer of a `.note` reads a file's size from its header too
  (`picture.image_size`).
- A stretch is `w`, `h` with an aspect other than the crop's: nothing to
  compute.
- `copied_box` for a picture (Gamma's own copy in `GammaNoteKey`) becomes
  `{content, picture}` and is used when the note still shows the picture
  at the same place, size and turn; the `pictures` part of the copy is
  keyed by UUID as now.
- The review's `pictures_imported` count and warnings stay; a warning kind
  goes ("rotated pictures are stored turned").
- **Pictures imported before this** stay text boxes holding an image, drawn
  and exported as today. A migration is declined: their pixels are already
  transformed and the crop is gone. The export keeps its `_PICTURE` path
  for them.

**The Notability export** (`notability_export`, `notability_write`): a
picture block becomes an `ImageMediaObject` with `rotationDegrees` from
`angle` (`_common` already writes `getattr(item, "rotation", 0.0)`),
`FigureCropRectKey` from the crop × the file's pixel size,
`isFlippedHorizontal` / `Vertical` from the flips, `unscaledContentSize`
and `documentOrigin` from `w, h, x, y` through the frame, the caption as
`captionFieldText` with `isCaptionEnabled`, and the file under `Images/`
as it is (a GIF as the still frame plus the asset, as now). `nw.Picture`
gains `rotation`, `crop`, `flip_h`, `flip_v`, `caption`. The round trip
of a picture is then exact where Notability keeps the field, and the
import's "A note Gamma wrote" rules apply.

## The iPad

Nothing in Swift for the first version: the file input opens Photos, the
camera and Files in a `WKWebView`, a paste from the system clipboard
carries the image to the page's `paste` event, and the Pencil taps and
drags the picture as a pen does. Two items for the device checks
([dev/ipad.md](../dev/ipad.md#device-checks)): the picker from the strip's
button, and a HEIC photo from Files, which Safari decodes and the page
re-encodes as JPEG. Later, `public.image` in the app's
document types, so a photo shared to Gamma from Photos becomes a picture
on a new sheet of the open notebook (`openedFiles.openedKind` answering
`"picture"`), and the Pencil's lasso taking pictures (phase 4).

## Phases

**Phase 1: the object on the page (web).** `gamma/picture.py` and
`markup/picture.js` with `tests/shared/picture.json` and
`picturemerge.json`; the `kind` migration step; `is_highlight` /
`isHighlightBlock`; `PictureLayer.jsx`, `usePictures.js` (App's side, on
the `useTextBoxes` pattern: one selection, `foldBlocks` for the fresh
picture, every edit through `setBlocks`), `TransformHandles.jsx` lifted
from the ink layer, `MarkupLayers` and `useMarks`, the strip's button and
More row (`MarkupToolbar.jsx`, `useInkStrip.js`, `P` in the key table and
[dev/hotkeys.md](../dev/hotkeys.md)), paste and drop, the client-side
re-encode; the generalised key-by-key merge on both sides; history labels
and runs; the notes row (marker, card, caption) and `showOnPage`; `NOT_INK`;
the dark-page rule. Tests: `frontend/tests/picture.test.mjs`,
`blockHistory`, `collabSession`, `blockOps`; `backend/tests/test_picture.py`
(normalize, merge through `/ops` and `PUT /blocks/{id}`, the kind column),
`test_shared_fixtures.py`; the browser scenario
`frontend/tests/e2e/scenarios/pictures.mjs` (add, move, resize, rotate,
crop, flip, undo, a finger, a stylus, two people on one picture) registered
in `select.mjs` for `frontend/src/markup/`, `ink/`, `notebook/`, `editor/`
and `gamma/picture.py`. Docs: `docs/dev/pictures.md` and its README row,
the user guide's "Draw and type on the page", cross-references in
text_boxes.md, handwriting.md ("Not built yet" loses nothing, `NOT_INK`
gains a line) and notebooks.md. The largest phase.

**Phase 2: the PDFs and the AI.** The `/Stamp` writer and its import, the
notebook PDF, the AI's page picture and `view_image`, the labels, the text
exports' lines. Tests in `test_picture.py` (geometry at every page
rotation with a MuPDF pixel check, as `test_text_box.py` does), the
export, Obsidian, Zotero, notes-PDF and AI tool label checks, the `imports`
tests for a Gamma stamp and a foreign one. Docs: pictures.md "Server and
interchange", [dev/pdf_typesetting.md](../dev/pdf_typesetting.md), the
import_export.md "in the exports" section.

**Phase 3: Notability both ways.** `picture_block`, the header-only size
read, `_store_picture` without decoding, `copied_box` for pictures, the
writer's fields, the round trip. Tests: `test_notability.py`,
`test_notability_samples.py` (the 16.1.5 sample has a cropped photo and
turned pictures, stored transformed today: they must now come in as their
original files with a `crop` and an `angle`, and the stored-size
assertions change), `test_notability_ntb.py`,
`test_notability_export.py`. Docs: import_export.md's Notability sections,
notability-format.md's "What Gamma decided" and "Still open" (the flip and
turn order is then exercised against the app, if a device is at hand).

**Phase 4: the long tail, deferred**, each small and independent: the lasso taking
pictures with strokes (a selection filter, GoodNotes-style: pictures move,
scale and turn with the ink, by the same handle values); a `z` key if
document order is not enough; lock; the iPad share-sheet photo; SVG on the
page; pictures in the zoom window; an opacity key.

## Still open, and what was confirmed

Reviewed 2026-10-25, all confirmed: the content as the caption, so a
caption can be typed into the row without touching the image syntax, the
file merges as a key, and a picture cannot degrade into a text box when a
regex stops matching; pictures under text boxes on one layer order
(photo, then typed text, then ink); uniform resize, Alt to stretch and the
90° magnetic snap beside the lasso's Shift 15°; HEIC and large photos
re-encoded in the browser, since the iPad's server could not carry
`pillow-heif`; old imported picture boxes stay as they are, with no
migration.

Open:

- Not decided here: the picture's look on dark pages beyond "not inverted";
  whether a picture's caption shows on the page (Notability draws it under
  the picture; Gamma's caption is in the notes, as ink's is).

## As built

Phases 1 to 3 were built on the `embed-images` branch in October 2026, with
these differences. Phase 4 waits
([dev/pictures.md](../dev/pictures.md#not-built-yet)).

- **The key is `I`, not `P`.** `P` already steps to the next pen and Shift+P
  to the one before (`ink/stripKeys.js`). The button's title says "Picture
  (I)". No row in the strip's More menu was made, nor a catalog command.
- **No Picture tool is ever armed.** The button opens the picker and leaves
  the armed tool as it was, as planned. So Undo follows a selected picture
  alone, not "the Picture tool armed".
- **The picker goes through `pickFiles`** (`shared/lib/pickFiles.js`), the
  rule for every file pick ([dev/ui-design.md](../dev/ui-design.md)), not an
  `<input>` kept in App.
- **With no surface in view**, a pick or a paste goes to the sheet among the
  notes that shows the most of itself.
- **`x` and `y` may be negative**, down to −`MAX_COORD`. The PDFs cut a
  picture at the page's edge, so what is kept on the page is the turned
  frame, not the frame before the turn. A wide picture turned 90° into a
  corner needs its own frame's corner off the page.
  - `keep_on_surface`, the handles, Fit to page and Reset follow the turned
    frame. `turnPicture`, `cropTo` and `resetPicture` take the surface.
- **The frame merges as its size and its centre**, not key by key. A resize
  or a turn about the centre rewrites `x` and `y`, so merged key by key, a
  resize would undo a move made meanwhile. `w` and `h` merge key by key, and
  the centre as one value. A crop's shift of the centre lands on a picture
  moved meanwhile.
- **EXIF orientation as browsers apply it**, not as Pillow's
  `exif_transpose` does: a JPEG's APP1 Exif block, and a PNG's `eXIf` chunk
  before its image data. A WebP's EXIF is ignored, as Chromium and Firefox
  ignore it. `image_size`, the PDF images and the import all follow that
  rule, so the crop's fractions mean the same image everywhere.
- **The 24 MP cap** holds for a file kept as it is. A re-encode is also
  bounded by the canvas caps, 16 Mi px in desktop Chromium and Firefox and
  8 Mi px and 4096 px an edge in WebKit (the iPad among it), since a canvas
  holds no more.
- **A late upload** takes the selection only when nothing else was selected
  meanwhile and no text field has the focus. The plan selected every new
  picture.
- **Edit shares.** A share visitor's fresh upload was refused until a saved
  block named it, so a dropped picture showed broken until a reload. The
  upload endpoints now remember the yes at once. The surface takes an image
  drag itself, since a share's app takes no files.
- **The handles lie over the boxes and the ink**, portalled into the
  surface's wrapper at z-index 7, so a box over a picture takes none of
  their presses. The crop's handles are 12 px with a 28 px hit area on every
  device, with no coarse-pointer size.
- **A new picture is an undo step of its own**, made through `setBlocks`;
  `foldBlocks` is not used. The labels gain "picture caption edit" and
  "picture change".
- **Highlights are written before the pictures** in the annotated PDF. The
  export wrote boxes and ink before the highlights, and the plan put the
  stamps before the boxes. It now writes the highlights and area notes, then
  the pictures, the boxes and the ink, pictures and boxes in document order
  (`notebook.document_order`), as the screen stacks them.
- **The stamp's `/Contents`** is the caption's plain text on one line.
  `/GammaPicture` also stores that `text`, so the import can tell another
  viewer's edit from a reference label, as for a text box.
- **A foreign stamp is placed from its appearance**, not upright at its
  `/Rect`: the `/BBox` through its `/Matrix` fitted to `/Rect`, the content's
  `cm`, then the page's turn, or upright for NoRotate. So it lands where
  viewers draw it on a turned page, and a Gamma export stripped of its
  `/GammaPicture` comes back turned and flipped.
- **The import reads an untrusted PDF within bounds.** Files are read
  lazily, once per image, only for the stamps added, within one decode
  budget. Streams are inflated by hand, and each image opens as the format
  its filter names. A stamp that does not read, or is past the budget,
  stays in the file. The storage quota is the one refusal storing a
  picture meets: a full workspace stops the import.
- **A JPEG stamp** whose EXIF orientation differs from the one the PDF drew
  it through gets its tag set to that one. A JPEG with a soft mask comes in
  as a PNG with its alpha.
- **`pdf_ops(picture, name)` takes no pixel size**: the crop is in fractions
  and an image fills the unit square.
- **`markdown_of(content, picture)`**, not `markdown_of(block)`, and the
  caption is read as Markdown first (`text_box.plain_text`). So an imported
  caption that came escaped is not escaped twice, and `**Bold**` reads
  `Bold`.
- **One AI label function**, `ai_context.placed_label`, names a box or a
  picture; there is no `picture_label` beside `text_box_label`.
- **Logseq writes the image line**, with no page. The plan wrote nothing
  there.
- **The notes PDF** draws the picture, then the grey "picture, p. N" line
  under it, then the caption.
- **Zotero** imports no annotation for a stamp. A picture the copy carries
  is a note too only when notes are under it, as a box is, and the README
  says once that Zotero shows pictures but keeps no annotation of them.
- **The Notability import**: `picture_block(media, frame, url, pixels,
  left_out)`, and `copied_picture` beside `copied_box`, with the copy
  `{caption, picture}`. An older export's `{alt, text_box}` copy gives its
  alt text as the caption. A cache of the note's files replaces
  `MAX_PICTURE_USES`, and `picture_bytes` and `MAX_PICTURE_PIXELS` are gone
  altogether. A file whose size does not read is left out with a warning,
  not kept whole. The storing is `note_app_import.AppImport.store_picture`,
  which the GoodNotes import shares.
- **The Notability export** writes an old picture box's alt text, less an
  Obsidian size, as the caption. Two pictures of one file are written as two
  files under `Images/`: whether Notability takes two media sharing one file
  is unknown.
- **The tests** are split three ways: `test_picture.py` (the block, its
  page, its line, the header size), `test_picture_merge.py` (the merge
  through `/ops`, `PUT /blocks/{id}` and the mirror) and `test_picture_pdf.py`
  (phase 2).
- **The two questions left open above** were settled so: on a dark PDF page
  a picture is dimmed to 88 % brightness, and the caption is not drawn on the
  page.
