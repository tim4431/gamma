# Notability's note format, and what Gamma's import reads of it

Survey 2026-10, for the Notability import
([dev/import_export.md](../dev/import_export.md#notability-notes)). Ginger
Labs publishes no format documentation: everything below is third-party
reverse engineering of files written by Notability 1.x (2013) to 16.5.3
(2026), checked here against the sources listed below. What a stroke stores,
beside GoodNotes, is in [ink-storage.md](ink-storage.md); this note is the
whole file.

Tags:

- **[V]**: read from the bytes of files Notability wrote, and confirmed by at
  least two independent sources, or by one with explicit byte evidence.
- **[V1]**: one source.
- **[I]**: inferred, consistent with the samples but not proven.
- **[C]**: the sources conflict; the resolution follows.

## Scope

- **`.note`**, Notability's "Note" export, is read: one note per file, with
  its PDFs, paper, handwriting, shapes, typed text, math, sticky notes,
  pictures and recordings.
- **`.ntb`**, the "NTB" export (Notability Cloud's format and the Mac app's
  Save As), is read into the same model (below, ".ntb"), all but its
  recordings.
- **Writing**: Gamma's Notability export writes `.note` files (below,
  "Writing a note").
- GoodNotes is out of scope.

## Sources

Facts only were taken from them; no code was copied.

| Source | Licence | What it covers |
|---|---|---|
| [gnnote's format document](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/notability-format.md), its [reader](https://github.com/jakubfabrici/notability-goodnotes/blob/main/gnnote/notability/reader.py) and [ecosystem notes](https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/ecosystem.md) | MIT | The fullest `.note` spec, tagged, from 21 samples (4.2 to 16.1.5): the zip per version, the archive, every ink array, groups, shapes, media, flow text, recordings, metadata, HandwritingIndex. The x inset from Notability's own PDF export matrix, template PDFs, bottom-aligned PDF slots, NSColor, decompression limits; the support articles on export |
| [notesconverter](https://github.com/samuelsadok/notesconverter/blob/master/converter/notability.py) | MIT | The most careful reader, checked against Notability's PDF exports (2013 to 15.3): per-anchor widths interpolated linearly, `3F − 2N = P`, PDFFile versions, `ceil` bottom-aligned slots, the 20/768 inset, dashes and shapes, text runs and list numbering, media keys, the crop path, fiducial page tests |
| [notability-to-pdf](https://github.com/lh/notability-to-pdf) (`NATIVE_PAGES.md`, `NOTABILITY_FORMAT.md`, `TEXT_RENDERING.md`, `CLOUDKIT_DISCOVERY.md`, its tools) | MIT | Page geometry measured on 6,726 renders and validated on 7,498 notes, the 768 fallback, `lockedWidth`, the indexed-container pitfall; its `NOTABILITY_FORMAT.md` has errors (colours as floats, styles as int32, style 5 an eraser) that its later documents and the others correct |
| [inkterop](https://github.com/cable729/inkterop) (`docs/formats/notability.md`, `ntb.py`, the erase audit) | docs CC BY 4.0, code MIT, fixtures CC0 | The `.ntb` container, op envelope, stroke payload and point blob, colour order; that erase, move and undo in the op log are unmapped |
| [nokcha0's converter](https://github.com/nokcha0/Notability-notes-converter) | none | Ink groups, shapes and `partialshape` paths, stickies, styles 5 and 6, `lineStyle2`, rotation in radians; `.ntb` event types and payloads with byte tests; that `curvesstyles` and `dashStyles` count strokes and that strokes, shapes and groups share one z-order, checked against the `.ntb` export of its example note. That note, its "Note" export (16.1.5) and its "NTB" export (16.10), is in `backend/tests/samples/notability/` for `test_notability_samples.py` |
| [bitsandbonds](https://github.com/bitsandbonds/notability-to-onenote) | none | An independent `.ntb` reading on 16.0.1 |
| [Notability's web app](https://notability.com/app/), fetched 2026-10-29: its generated FlatBuffers code (`CalendarEventHelpers-DA_idU3b.js`), its renderer (`store-Brjjb5AZ.js`) and its PDF export (`App-BTqAyZO1.js`) | © Ginger Labs | The `.ntb` op log's schema: every record kind by name (3 CreatePage, 7 InsertChar, 8 InsertString, 12 ModifyStyle, 17 ModifyInk, 22 CreateBlock, 23 ModifyBlock, 24 ModifyPositions, 25 DeleteEntities…) and the fields of each; that a crop is measured on the image as flipped. The web client writes the same log, so where no bundle shows a field this is the only source |
| [NotabilityEditor](https://github.com/SeedStudent/NotabilityEditor) (commit `e5d0bcc`) | 0BSD | An `.ntb` editor that replays edits (23) and moves (24) onto blocks, from the same schema; a block's sort key (9) and lock (20); `assets/official_templates/noteBundle_3.bin`, a bundle with an erase log |
| [OpenNote](https://github.com/GGStonks/OpenNote) (`src/import/notability.ts`) | none | A `.note` reader that applies a picture's crop only when its Figure holds the crop path (`$0`) |
| [notability-lib-private](https://github.com/TheNoim/notability-lib-private) (commit `85d2fa0`) | none | Sample notes of 4.2.5 and 9.3.4: a picture's smaller copy (`contentSnapshot`) and an uncropped picture's crop path |
| [note-core-swift](https://github.com/PsychQuant/note-core-swift) | none | The recording timeline and the curve UUID join |
| [notability2pdf](https://github.com/wongkang01/notability2pdf) | none | A 941-note corpus of formats 7–9; MathMediaObject; recordings |
| [jvns' post](https://jvns.ca/blog/2018/03/31/reverse-engineering-notability-format/) and [svg2notability](https://github.com/jvns/svg2notability) | none stated / none | The first reverse engineering (7.x); RGBA colour bytes |
| [alicja's post](https://alicja.dev/blog/2019/02/27/retrieving-drawings-from-Notability.html), [notability2svg-python](https://github.com/mrandri19/notability2svg-python), [zathura-note](https://github.com/marvinborner/zathura-note) (WTFPL), [NotabilityViewer](https://github.com/GZhonghui/NotabilityViewer) (MIT), [notability-reader](https://github.com/xrayshan/notability-reader) (none) | as listed | Weak sources: fixed `$objects` indices; one sample listing |
| [notability-to-svg](https://github.com/HuyNguyenAu/notability-to-svg) | GPL-3.0 | The pitfall of scanning `$objects` for the first ink (it finds a text box's empty overlay) |
| [NotabilityShortcuts headers](https://github.com/itscrystalline/NotabilityShortcuts/tree/master/NotabilityHeaders) | none (a class dump) | Class and ivar names |
| Ginger Labs' [Sharing Notes](https://support.gingerlabs.com/hc/en-us/articles/205228298-Sharing-Notes) and [Backing Up Notes Outside of Notability Cloud](https://support.gingerlabs.com/hc/en-us/articles/9591149777178-Backing-Up-Notes-Outside-of-Notability-Cloud) | © Ginger Labs | Export formats and where Export Options is |

## The container

- A `.note` is a zip [V]. The app writes stored members with UTF-8 names
  and explicit directory entries; 15.3 and later also write one member with
  an empty name. Files re-zipped by a user are deflated, and the app reads
  them.
- All members sit under one top folder, usually named after the note but
  sometimes after an older name of it. The rule: the shallowest member named
  `Session.plist` marks the note's folder, and every other path is resolved
  below it, without case as a fallback [V]. iCloud Drive keeps a note
  unzipped as a `<Name>.nbn/` folder with the same members [V1].
- Members: `Session.plist` (the whole document), `metadata.plist`,
  thumbnails, `Recordings/library.plist` and the audio, `Images/`,
  `PDFs/<UUID>.pdf`, `Assets/<id>/media.gif` (an animated GIF's source),
  `HandwritingIndex/index.plist` (recognised handwriting with a box per
  character) and `NBPDFIndex/` (a search cache, with a nested
  `PDFIndex.zip`, possibly stale). Gamma reads the first five kinds and
  never opens the search cache.
- Detection by content, not extension: Supernote's `.note` starts with
  `noteSN_FILE_VER_` [V1]; a `.ntb` is a zip with a root member named
  `noteBundle`; Notability's "PDF + Recording" backup is a zip of a PDF and
  `.m4a` files, neither.

## The archive

- `Session.plist` is a binary plist keyed archive, `$archiver`
  `GLKeyedArchiver` (Ginger Labs' subclass of NSKeyedArchiver) and `$top`
  `{"$0": UID}`; `metadata.plist` and the archives inside ink groups use
  `NSKeyedArchiver` and `{"root": UID}` [V].
- Values are UIDs into `$objects` or inline: GLKeyedArchiver inlines numbers,
  booleans, data and short strings, so `numcurves` is a UID to an int while
  `curvespoints` is inline bytes. Dereference in a loop [V].
- Objects carry `$class` → `{$classname, $classes}`; `$classes` may be
  missing, and Swift classes carry a module (`Notability.PaperAttributes`,
  `GLModel.PaperAttributes`): match names without it [V].
- **Containers come in two forms**, not tied to the version: `NS.objects`
  (with `NS.keys` for a dictionary), or indexed `NS.object.N` /
  `NS.key.N` keys in any order. 7.2.5, 8.4.8 and 10.2.4 samples use the
  indexed form. A reader of one form sees an old note as empty
  (notability-to-pdf's bug) [V].
- Strings are a str, `{NS.bytes: utf-8}` or `{NS.string}`; compare them NFC
  [V]. Dates are `{NS.time}`, seconds since 2001-01-01 UTC. Colours are
  UIColor `UIRed`/`UIGreen`/`UIBlue`/`UIAlpha` (15.3 adds `-Double`
  keys), greyscale `UIWhite`, or a Mac 4.x NSColor's ASCII `NSRGB`;
  text runs also carry an "r,g,b,a" string with 15 decimals [V].
- Geometry is strings: `{x, y}`, `{{x, y}, {w, h}}`, `{location, length}`.
  The plain plists inside (dash styles, shapes, groups) use numeric arrays
  [V].
- Cycles are possible and a legitimate path reaches about 17 levels; a depth
  guard of 16 cut real data in one reader. Gamma never walks the graph: it
  reads named keys from the root and checks each value's type where it
  reads it [V].
- Never find the ink by scanning `$objects`: every text box holds a full
  FormattedString with its own empty ink overlay, which can come first [V].

## The root and versions

- The root is a `NoteTakingSession`: `richText` (a FormattedString, the
  whole document), `name`, `packagePath`, `subject`, `creationDate`,
  `sessionFormatVersion`, `paperIndex` and `paperLineStyle` (11.x and
  earlier), `NBNoteTakingSessionDocumentPaperLayoutModelKey` (11.x and
  later: the paper and a page list), `contentPlaybackEventManager` (the
  recording timeline) [V].
- `richText` holds `'Handwriting Overlay'` (with its space) →
  `SpatialHash`, `reflowState`, `pdfFiles`, `pageLayoutArray`,
  `mediaObjects`, `attributedString` [V].
- `sessionFormatVersion`: absent in 2013–14, 4 for 7.x–8.x, 5 for 10.x, 7
  for 11.x, 8 for 12.x–14.x, 9 for 15.x–16.x [V]. What changed: `curvesstyles`
  from 10.x; force, tilt and curve UUIDs from 11.7.2; the paper model from
  11.x; `GLModel.` classes from 15.3; ink groups, the pencil and stickies
  from 16.x.

## Pages and geometry

- **One canvas.** The note is one continuous 2-D canvas in document units,
  y down, its pages stacked without gaps [V].
- **Page width W**: `reflowState.pageWidthInDocumentCoordsKey`
  (`NBReflowStateLocked`); else the `W` of `paperSizingBehavior`
  `lockedWidth:<W>:<device>`, which agrees with it in 2,179 of 2,179 notes;
  768 when there is no reflow state (2013–14); W below 16 is damage. 565 is
  the usual iPad value [V].
- **The inset**: x = 0 is not the paper's edge. The paper spans
  `[−W × 20/768, W − W × 20/768]` on plain and PDF pages alike: Notability's
  own PDF export draws ink with the matrix `[612/W 0 0 612/W 15.9375 …]`
  and the PDF at x = 0 [V, three sources]. There is no vertical offset [V].
- **The page list**: the paper model's `pageLayoutArray` when it is not
  empty (validated on 7,498 notes), else `richText.pageLayoutArray`; the
  two agree on every shared sample [C, minor]. An entry names a PDF page
  (`kPageLayoutPDFPageNumberKey` and `kPageLayoutPDFFileNameKey`, or
  `kPageLayoutPDFFileKey` → a PDFFile in 10.4 and earlier) or is blank (page
  number INT64_MAX, 0x7fffffff in 2013). Several PDFs, any order and
  repeats are allowed. The list may be shorter than the content (implicit
  plain pages below) or end in empty blank pages, which the export leaves
  out [V].
- **A plain page** is `1.3125 W` tall (803.25/612): every legacy thumbnail
  has this shape and Notability's export is 612 × 803.25 pt [V]. An A4 note
  is 595.28 × 841.89 pt [V1]. A static-width letter note is `W × 11/8.5`,
  which gnnote and nokcha0 floor to a whole unit while an `.ntb` page is the
  unfloored 789.41 [C].
- **A PDF page, PDFFile version 2 (10.x and later)**: scaled to width W,
  bottom-aligned in a slot of `ceil(h)` units, rounded per page. Without the
  rounding a 30-page note drifts about 30 units [V]. **Version 1** (8.x and
  earlier): drawn 8.5 in wide, scaled down when wider and centred; an 8.4.8
  720 × 540 page takes `ceil(540 × 565/720) = 424` units, which only the
  scale-down produces (notesconverter clips instead) [C, resolved].
- The page's size as displayed: the crop box (else the media box), its
  sides swapped on a quarter turn [I].
- **Worked**: W 565, plain: H 741.5625, scale 1.08319; ink at (100, 835) is
  on page 2 at 124.26, 101.21 pt. W 565, an A4 PDF page: h 799.066, slot
  800, top gap 0.934, scale 1.05359. W 565, a 720 × 540 version 1 page:
  slot 424.
- **Paper**: 11.x and later, `PaperAttributes`: `paperIdentifier`
  (`Color:#RRGGBB`, `TemplatePDF:<UUID>:#RRGGBB`, `Legacy:<n>`),
  `paperOrientation`, `paperSize` (letter, a4, legal), `paperSizingBehavior`,
  `lineStyle2` (`Lines:<b>:<b>:<spacing in inches>` or `No Lines`). Earlier,
  `paperIndex` (12 white, 0 the tinted 2014 paper) and `paperLineStyle` (5 a
  small grid). The line colour and margins are not stored [V].

## Ink

The per-stroke model, beside GoodNotes, is in
[ink-storage.md](ink-storage.md#notability-note-ntb). In the file:

- All curves of a hash are parallel little-endian arrays [V]: `curvesnumpoints`
  (int32 per curve), `curvespoints` (float32 x, y), `curvesfractionalwidths`
  (float32 per anchor), `curveswidth` (float32 per curve), `curvescolors`
  (RGBA bytes per curve, not floats), `curvesstyles` (one byte per stroke,
  below, not int32), and from 11.7.2 `curvesforces`, `curvesaltitudeangles`
  (radians) and `curvesazimuthunitvector` per anchor (1.0, π/2 and (1, 0)
  when unknown).
- **A stroke is a curve, or the curves the eraser cut it into**: those
  follow each other and share an entry of `curveUUIDs` (16 bytes per curve,
  from 11.7.2), and `curvesstyles`, `options` and the keys of `dashStyles`
  count strokes [V]. The 16.1.5 sample has 596 curves in 149 runs of one
  UUID, and 149 styles: read by stroke, its highlighters are exactly its
  translucent curves; read by curve, 4 curves disagree and 8 strokes
  change tool partway. gnnote read the same array as cut short; nokcha0
  (against the note's `.ntb` export) and Gamma read it by stroke. A hash
  with a style byte for every curve, or without UUIDs, counts curves [I].
- A curve has `1 + 3k` points, a C1 Bézier chain (anchor, control, control,
  anchor…), `k + 1` anchors, and `3F − 2N = P` holds; a curve whose count is
  not `1 + 3k` is a polyline with every point an anchor (gnnote's
  tolerance) [V]. The worked bytes (one curve: points (10,20) (12,20)
  (14,22) (16,22), widths 1.0 and 0.5 at a base of 1.5, red, a pen) are
  pinned by `test_notability.py`.
- The width at an anchor is `curveswidth × fractional`, linear between
  anchors; that matched Notability's own PDF export [V].
- Styles: 3 pen, 4 highlighter (alpha 0x6b ≈ 0.42 from 10.2.4, 0x44 in
  8.4.8), 5 pencil [V1/I], 6 not drawn by Notability's export [V1]. Without
  the array (8.4.8 and earlier), or past its end, a translucent curve is a
  highlighter [V].
- `dashStyles`: a plain plist mapping a stroke's number in its hash to
  pattern 1 (dashed) or 2 (dotted); no lengths are stored [V].
- `groupsArrays` (16.x): each a plain plist `{inkGroup: {inkGroupObjects,
  transform: [a, b, c, d, tx, ty]}, index}`; a member of type 1 is a whole
  keyed archive of another hash, of type 2 a shape list of its own,
  `{shapes, kinds, indices}` as a hash's (below) [V: the 16.1.5 sample's 70,
  which gnnote calls shape dicts]. Groups nest. The index is the group's
  position in its hash's z-order (Shapes, below) [V]; its members are
  drawn there in their order [I].
- `eventTokens` (per curve) and `options` (per stroke) are not drawing
  data, and `curveUUIDs` only joins curves into strokes and to recordings.

## Shapes

- `shapes` is a plain plist of parallel `shapes`, `kinds` and `indices`
  (z-order) lists, separate from the curves [V]. Each has `appearance`
  (`style` 3 pen or 4 highlighter, `strokeColor.rgba`, `strokeWidth`,
  `fillColor`, `dashStyle` with `pattern` and `arrowHead`).
- Kinds: `line` (`startPt`, `endPt`, an optional quadratic `controlPoint1`;
  an arrow's head is not stored), `rectangle` and `square` (4 corners, maybe
  turned), `triangle`, `polygon`, `circle` and `ellipse` (the 4 corners of
  the turned rectangle it is inscribed in), and `partialshape` (a shape
  partly erased) [V]. Style 5 is a pencil shape here too [V1].
- A `partialshape` keeps two archived CGPaths: `outlinePath`, what is left
  of the shape's centre line, a piece per move, and `strokePath`, the
  outline Notability fills [V on the sample's 23]. Each is the bytes
  `25 b3 e5 48`, the element count (uint32), a byte per element
  (CGPathElementType: 0 move, 1 line, 2 quadratic, 3 cubic, 4 close; the
  sample has 0, 1 and 3), then the elements' points as float64 x, y pairs,
  little-endian [V]. `rect`, `rotatedRect` and `extremePoints` bound it.
- **One z-order**: a shape's index and an ink group's are positions in one
  z-order of their hash, and its strokes take the other positions in turn
  [V]. In the 16.1.5 sample, 88 shapes and 4 groups take 92 distinct
  positions below 241 and its 149 strokes the rest; nokcha0 found the same
  against the note's `.ntb` export, whose events come in that order.

## Typed content

Four kinds [V]:

- **Flow text**, `richText.attributedString`: typed on the page itself. It
  has no stored position: Notability lays it out from the top of page 1 and
  users push it down with blank lines. Only fitted layout constants exist.
- **Text boxes**, `TextBlockMediaObject`: `documentOrigin` (top-left before
  rotation), `unscaledContentSize`, `rotationDegrees` (which holds radians,
  about the centre), `zIndex`; the text in `textStore` (a FormattedString),
  inset 5 × 2 units; the fill `paperStyleObject.paperColor` (nil:
  transparent).
- **Math**, `MathMediaObject` (11.7+): `latex` and `color`; no rendering is
  stored.
- **Sticky notes**, a `CanvasMediaObject` without text (16.1.5): a coloured
  rectangle (default `#FFF48E`) with its own ruling; ink on it stays in the
  main overlay [I].
- An attributed string is `stringKey` and `subRangesKey` runs: a
  `{location, length}` range in UTF-16 units, the font
  (`NSFontNameAttribute`, `NSFontSizeAttribute`), the cross-platform
  colour, and other attributes (`NSStrikethrough`, `highlight`,
  `checklist-checked`, `indent-level`, `indent-decoration-style` 0–3,
  `indent-decoration-number`). Bold and italic exist only as the font
  name's suffix (`-Bold`, `-Italic`, `-Oblique`).

## Pictures

- `ImageMediaObject` (and `GifMediaObject`): the image is
  `figure.FigureBackgroundObjectKey.kImageObjectSnapshotKey.relativePath`
  (full resolution), and `contentSnapshot.relativePath` a smaller copy;
  `imageIsMissing` means Notability lost the file [V].
- Gamma takes the full-resolution image when the note has its file and it
  is not flagged missing, else the smaller copy on the same terms, else an
  animated GIF's `Assets/<id>/media.gif` alone
  (`notability._Reader._picture`, `_image`), and with none of them names
  the picture missing. gnnote takes the copy only when the full path is
  empty. A copy or an animation standing in (`Media.stand_in`) is shown
  whole, since the crop is measured on the image the note lacks. The crop
  is kept only when `notability_map._whole` finds it is all of that image
  (at its origin, with the stand-in's aspect, to within whole-pixel
  rounding); otherwise the report says so ("crop").
  4.2.5 and 9.3.4 write the copy (TheNoim's notability-lib-private notes),
  and for an uncropped picture it is the whole image scaled evenly (3000 ×
  2000 to 768 × 512, and to 432 × 288) [V1]; 10.2.4 and 16.1.5 write none.
  No sample has a cropped picture's copy, so that one is the whole image
  too is inferred [I].
- `FigureCropRectKey` is the crop in source pixels, the whole image when
  uncropped; `unscaledContentSize` the shown size after the crop and before
  the turn; `isFlippedHorizontal`/`Vertical` the flips (the feature from
  14.4.6, December 2023, by Ginger Labs' App Update History; the keys seen
  in files from 15.3) [V]. [V on the sample's thumb12x.png: the flips about
  the frame's own axes, then the turn; a higher zIndex on top. I: the crop
  before the flips, since no sample picture is both cropped and flipped.]
  `test_notability_samples.test_the_example_s_pictures_lie_where_notability_drew_them`
  pins it: page 1's four pictures as Gamma draws them differ from the
  thumbnail by a mean of 4 to 5 (of 255), and the two flipped ones turned
  the other way, which is the other order, by 49 and 65.
- A cropped picture's Figure holds the same rectangle again under `$0`: an
  archived CGPath laid out as a `partialshape`'s (Shapes, above), a move to
  its top-left and four lines round it back there, five float64 points in
  93 bytes. An uncropped picture has null there [V on the 16.1.5 sample and
  notesconverter's 10.2.4 `image-insert.note`]; 9.3.4 writes the whole
  image's path [V1]. notesconverter and OpenNote apply a crop only when
  `$0` is there; nokcha0, notability2pdf and NotabilityEditor apply
  `FigureCropRectKey` whatever `$0` holds. Gamma's import reads
  `FigureCropRectKey` and ignores `$0`.
- An animated GIF's still frame is in `Images/`, the animation in
  `Assets/<id>/media.gif`: its object says `isGif` and names `<id>` as its
  `assetsIdKey` (every picture has one; only a GIF's names a folder), its
  crop in the still frame's pixels; `remoteUrl` is where it came from [V on
  the sample, whose 352 × 240 GIF has a 220 × 150 still].

## Audio and the stroke timeline

- `Recordings/library.plist` (XML) lists the recordings by identifier with
  `filepath`, `name`, `userFacingName`, `createddate` and `duration`; the
  audio is `Recordings/<filepath>`, AAC in MPEG-4 (`.m4a`) in every sample.
  The library or the audio can be missing while the timeline still names
  them [V].
- `contentPlaybackEventManager` keeps one event per stroke drawn during a
  recording, as arrays: seconds into the recording, a duration (0.05–1.9 s),
  the recording's identifier, and the join to a curve (`eventTokens` in 10.4
  and earlier, `curveUUIDs` from 11.7.2) [V on 8.4.8 and 11.7.2]. The join
  is verified only for 10.4 and earlier. No time is stored for a stroke's
  samples.

## Metadata

`metadata.plist` is a SessionInfo: `noteName`, `noteSubject` (the folder;
`unsortedNotesKey` or `Unfiled Notes` mean none, and notes since the CloudKit
migration always say `unsortedNotesKey`), `noteCreationDateKey`,
`noteModifiedDateKey`, `noteHasRecordingKey`, `uuidKey` [V].

## .ntb

Three decoders read it independently (inkterop on 16.5.3 with calibration
strokes, bitsandbonds on 16.0.1, nokcha0 on a note exported both ways) and
agree where they overlap [V]:

- a stored zip of `version`, `manifest.json`, `noteBundle` (FlatBuffers),
  `thumbnail.png`, `assets/<sha512>.<ext>` and recognition indexes;
- `noteBundle` is an append-only log of ops, oldest first: document, text,
  styles, ink (type 15), shapes (18), blocks (22). An ink op carries its
  page, origin, turn, scale, tool, dash, RGBA, base width and a point blob
  of offsets from the origin with, per anchor, two float16 values and two
  bytes (the second float the force; the first byte, which renderers read
  as a pencil's opacity, is the altitude).

What Gamma reads (gamma/notability_ntb.py), from nokcha0's description and
the bytes of its example, which the "Note" export of the same note checks,
with the names and fields of Notability's web app's schema (Sources) [V on
that sample unless tagged; "the schema" where it is the only source]:

- **The root** table: field 4 the creation time in ms, field 6 the records.
  A record: field 0 its id (two uint32), 1 a time, 4 its kind (a byte), 5
  its payload, a table. A **position** is an inline struct of an id and a
  uint32 count: a page is the page list's id (the kind 3 record) and the
  page's index, a text offset an anchor's id and UTF-16 units past it.
- **The document** (kind 1): field 0 → its title; 1 → 0 → the layout, its
  page size at 3 (679 × 891.1875 in the sample) and its paper at 0 (0 the
  pattern: 0 ruled, 1 dots, 2 grid [V1 for 1 and 2]; 1 the spacing, in
  units of a page W wide, so ÷ (W / 8.5) is inches; 4 a colour; 5 the
  legacy paper); 4 the font, 5 its size.
- **Ink** (15): 0 the page, 1 the origin, 2 the turn about it, 3 a scale of
  the points and the width, 4 the tool (0 pen, 1 pencil, 2 highlighter), 5
  the line style (2 dashed, 3 dotted), 7 RGBA bytes, 8 the width, 9 the
  path: a precision byte (0 half floats, 1 floats), the point count
  (uint16), the kind (3 cubic, 7 linear), then the first point, zero in
  every sample, and offsets from the origin. A cubic path is a node, a
  command (0 a segment's three points, 3 a move to the next piece's
  first) and its points per anchor after the first, then the last node; a
  node is the width multiplier and force (half floats), the altitude (a
  byte of π/2) and the azimuth (a byte of a turn). A linear path is a
  command (6 a line, 7 a move) and a point each. A move is where the eraser
  cut the stroke: its pieces become the curves a .note keeps under one
  UUID, and a partly erased shape is an ink record of linear pieces (the
  sample's 23 `partialshape`s are 23 of its 249 ink records).
- **Shapes** (18): 0, 1 and 2 as ink's; 4 the kind (1 a line: 0 its start,
  1 its quadratic control, 3 its end; 2 a polygon: 0 its points; 3 an
  ellipse: 1 the size of its box), 6 the tool, 7 the dash, 9 RGBA, 10 the
  width, 14 a pencil shape's force [V1]. Fills and arrowheads: no sample
  has them, so their fields are not known.
- **Groups** (20): 0 the members' ids; the members carry their own turn.
- **Blocks** (22, CreateBlock: a picture, a sticky note or a text box): 0
  the type (0 text, the default, 1 an image, 2 a formula; 1 on every
  sample picture and absent on its stickies), 2 the page, 3 the frame's
  top-left corner as turned about its centre, 4 the turn, 5 a scale (two
  float32, 1 and 1 when absent; the frame shown is the size times it), 6
  the size, 8 whether a picture's caption shows (a byte, absent false), 9
  the sort key, 10 → 0 the asset (0 its SHA-512, 1 the same file's name in
  a .note, 2 its MIME type, 3 its size in bytes) and → 1 its logical size,
  11 a crop in that logical size (the sample's GIF is 352 × 240 px, its
  logical size 220 × 150), 15 a sticky's paper (4 its colour), 16 and 17
  the horizontal and vertical flips (bytes, absent false), 20 a lock (1 on
  the sample's two locked pictures). The schema requires 2, 3 and 6. Not
  read: 1 the corner style (0 square, 1 round), 7 text wrap, 12 a web URL,
  13 and 14 a formula's LaTeX and colour, 18 a width that fits the text,
  19 margins, the lock, and the asset's name and size. [The schema for 5,
  8, 16 and 17, which no sample has.]
- **A block's text**: a block with an asset, or of type 1, is a picture,
  and the text it holds is its caption, shown when 8 is set (plain, its
  line breaks kept); any other block holding a text is a text box [V1: no
  sample has one], and one with a paper a sticky note.
- **A crop on a flipped picture** is measured on the image as flipped
  [the schema: the web app's renderer (`refreshSpriteLayout`) and its PDF
  export both take a flipped picture's source rectangle from the far side
  of the image; no sample picture is both cropped and flipped]. The reader
  mirrors it onto the image as stored, in the logical size W × H: x
  becomes W − x − w when the picture is flipped horizontally, y becomes
  H − y − h when vertically. An asset with no logical size has its crop in
  pixels the reader does not know, and it is kept as it is.
- **Text** (8, InsertString): 0 where it is inserted (a position: the
  record it counts from and an index into its string), 1 the string, 2 the
  text field, the block it is typed in (none: the document) [the schema;
  the sample's one text record has 1 alone]. Its id is an anchor at where
  it starts. The insert position is not read: a text is appended to its
  owner's, so one typed inside earlier text comes in at the end of its
  owner's text, unwarned. Kind 7 is InsertChar (0 its position, 1 a
  Unicode scalar, 2 the text field): the reader takes its position as an
  anchor that marks a range's last character and adds no character [V1:
  the sample's one adds a newline (10) after a text that ends in one, so
  the sample cannot settle what an insert position means].
- **Character styles** (12): 0 and 1 the range, then a table each: 3 bold,
  4 italic, 5 underline, 6 highlight, 7 the font, 8 its size, 9 the
  colour, 10 a link, 11 superscript, 12 subscript, 13 strikethrough, 14
  code [the schema]. The reader takes bold, italic, the size, the colour
  and strikethrough; superscript and subscript are not imported. The
  sample's 15 style records set none of 11 to 13, which is how nokcha0's
  "11 strikethrough" went unchecked. Paragraph styles (13): 2 the indent
  level, 4 the line spacing, 5 the list (1 bullet, 2 numbered). No number
  is stored: a list counts its paragraphs per level, bullets too, as the
  .note's numbers do.
- **A log** [V1: the sample is a compacted list, one version of each
  record and no edit, move or erase record; the rest is the schema's]. The
  reader replays it in its order (`notability_ntb._live`,
  `_Reader._blocks`):
  - **Versions**: a record given again is current at its last version, in
    the place of its first.
  - **Erase records** (25, DeleteEntities): 0 the records deleted and 1
    those restored (vectors of ids), 2 the pages deleted and 3 those
    restored (vectors of positions, 12 bytes each). A record deleted at
    the end is gone, with the text records whose text field names it. A
    page deleted at the end is only counted ("deleted page"): a record's
    page is its index in the page list, so the pages after it would have
    to be numbered again. NotabilityEditor's `noteBundle_3.bin` has a
    sticky note deleted, restored and deleted again, its page vectors
    empty.
  - **Edits** (23, ModifyBlock) change each block they list: 0 the blocks
    (a vector of ids, the one field required), 2 the page, 3 the turned
    frame's top-left corner, 4 the turn, 5 the scale (replacing the one
    before), 6 the size, 8 whether a caption shows, 9 the sort key, 12 the
    crop, 13 a sticky's paper, 14 and 15 the flips. 1 the corner style, 7
    text wrap, 10 and 11 a formula's, 16 a width that fits the text and 17
    the lock change nothing Gamma shows.
  - **Moves** (24, ModifyPositions): 0 a vector of moves, each 0 its
    target (required), 1 the page, 2 the turned frame's top-left corner, 3
    the turn, 4 the scale, 5 the sort key, applied to each target that is
    a block. A record whose targets include a stroke, a shape or a group
    is not applied to them and is counted once ("ink edit"): they come in
    where they were drawn. Their changes (17 ModifyInk, 19 ModifyShape, 21
    ModifyGroup) are records of a kind not read.
  - **Tables of one value**: an edit's or a move's turn, scale, crop and
    paper are each a table of one (SetFloat, SetSize, SetRect, SetPaper).
    An absent field keeps the value, and a table holding none clears it:
    no turn, a scale of 1, no crop.
- **Order**: strokes, shapes and groups are drawn in the order of their
  records, a group in its first member's place, as the .note's z-order
  has them, except highlighter shapes: the sample lists its 16 apart,
  marked by a field 12 of 999 999, while the .note stacks them among the
  rest.
- **PDF**: the one PDF under `assets/` is the note's first pages, all of
  them in order; several are not placed [V1].

Not read: recordings (no sample has one), a checklist's ticks, a highlight
on text, and where the pages of several PDFs go. Gamma imports an `.ntb`
since 2026-10, the decoders having closed most of what the verdict before
left open: the example's NTB export reads as its Note export does,
stroke for stroke within a point, the same notes (but for a bold the .ntb
keeps where the .note names the font alone, Impact) and the same pictures,
but for their flips: the bundle has no flipped picture, and its own
`thumbnail.png` (16.10) draws the Note's two flipped ones unflipped. The
log's edits, moves, deletes and restores are replayed as the schema has
them (A log, above), but for ink: a stroke, shape or group moved or
changed later comes in where and as it was drawn, which the report says.

## Writing a note

Gamma's Notability export writes `.note` files
([dev/import_export.md](../dev/import_export.md#notability-export),
gamma/notability_write.py). What is known of what the app accepts:

- **Ink is proven** [V1]: gnnote's proof of concept wrote strokes into a
  10.4 template (format 5, GLKeyedArchiver, `$top` `$0`): the parallel
  arrays with exact lengths and the counts to match, `eventTokens` all −1,
  `curvesstyles` 3 and 4, empty `groupsArrays` and
  `bezierPathsDataDictionary`, a random version 4 `uuidKey`, white
  thumbnails; the current iPad app opened it. Its recipe for PDF pages,
  pictures and text boxes was never tried in the app [I].
- **Gamma writes what 16.1.5 writes** instead, the sample's Note export
  taken class for class and key for key [V against the sample; not yet
  opened in the app]:
  - strings always behind a UID, numbers and booleans inline, except the
    ink counts and the shape list's and dash styles' data, which are behind
    one; a dictionary's values always behind one;
  - `$classes` as the app writes them: none for InkedSpatialHash, a class
    of the app's own Swift module listed without the module
    (`Notability.NoteDocumentPaperLayoutModel` →
    `NoteDocumentPaperLayoutModel`), a GLModel one with it;
  - a UIColor's components as floats, a `-Double` key with the exact value
    where the float is not it, `NSRGB` with three significant digits and
    the alpha only when it is not 1;
  - `options` 8 zero bytes a stroke, as the sample's are; no
    `eventTokens` (16.1.5 writes none); `bezierPathsDataDictionary` empty
    (the app's cache of stroke outlines, which the sample lacks for its
    first 64 curves);
  - a stored zip with Unix attributes and a directory entry per folder.
- **Written from gnnote's description**, no sample having one [I]: a
  TextBlockMediaObject (a CanvasMediaObject's keys and a `textStore`, a
  whole FormattedString with an empty ink hash and an
  NBReflowStateReflowable), a PDFFile (`pdfFileName` a UUID name,
  `contentBoxVersion` 1, `highlights` [], `version` 2, `type` 0), 15.3's
  page layout entries (`kPageLayoutPDFPageNumberKey`,
  `kPageLayoutPDFFileNameKey`, `kPageLayoutDocumentPageNumberKey`,
  `kPageLayoutPDFIsOriginalPageKey`, `kPageLayoutPageIsBookmarkedKey`), in
  both page lists.
- **A picture's turn, crop, flips and caption** go in the keys the
  sample's pictures carry [the keys V against the sample; these values not
  yet opened in the app]:
  - `rotationDegrees`: the turn in radians, clockwise as seen;
  - `FigureCropRectKey`: the crop in the file's pixels, the file's size read
    as a browser shows it (its EXIF orientation applied), the whole image
    when uncropped;
  - `$0` beside it: the crop again as an archived CGPath
    (`notability_write.crop_path`, magic `notability.PATH_MAGIC`), laid out
    as a `partialshape`'s: the element count 5, the bytes 0 1 1 1 1 (a
    move to the top-left corner and four lines round the rectangle back
    there), then five float64 x, y pairs, 93 bytes in all. A picture shown
    whole has the null UID there, as 16.1.5 and 10.2.4 write it (9.3.4
    writes the whole image's path). For the same crops Gamma's bytes are
    those of the 16.1.5 sample and of 10.2.4's `image-insert.note` [V].
    notesconverter and OpenNote apply a crop only when the path is there;
  - `isFlippedHorizontal` and `isFlippedVertical`: the flips;
  - `captionFieldText`: the caption's plain text on one line, its Markdown
    marks dropped, with `isCaptionEnabled` true; with no caption, the null
    UID and false.
  - `documentOrigin` and `unscaledContentSize` are the frame's top-left
    before the turn and its size after the crop, a stretch included. A
    page's pictures come before its text boxes, and both before its sticky
    notes, so their `zIndex` is lower: a sticky lies over what it covers,
    as on Gamma's screen.
  - An animated GIF is its still frame under `Images/` and its animation
    as `Assets/<id>/media.gif`. Two pictures of one file are two files.
- **Not written**: a formula as a MathMediaObject (no sample shows its
  keys beyond `latex` and `color`, so it goes as a text box of its TeX),
  thumbnails but `thumb12x.png`, `HandwritingIndex`, the recording
  timeline's events.
- **Gamma's own keys** [I]: a curve UUID beginning `GAMMAI` (a version 8
  UUID), which the app keeps as a stroke's identity, as it keeps its own;
  and the root's `GammaNoteKey`, data no class of the app decodes, which
  keyed unarchiving skips and the app's next save, writing its model
  again, leaves out.

## What Gamma decided where the sources conflict or say nothing

- **A static-width letter page's height** is floored to a whole unit, as
  gnnote and nokcha0 read `.note`; an `.ntb` states its own (789.41 in those
  exports, 0.4 units a page more), and Gamma keeps that.
- **An `.ntb`'s order** is its records' (a group at its first member's
  place), so its highlighter shapes, which it lists apart, may lie
  elsewhere among the strokes than the same note's .note stacks them;
  drawn translucent, that hardly shows.
- **Which page an item is on** is the page holding the vertical centre of
  its bounding box (notesconverter's mean, not gnnote's first anchor), so a
  stroke that starts in a page's last line stays on that page. An item is
  never split: lh and nokcha0 clip a crossing stroke onto both pages, gnnote
  keeps it on one; Gamma keeps it whole on one, where the screen cuts it off
  at the page's edge.
- **Landscape** takes the inverse of the portrait ratio at the same width
  [I].
- **The force scale** is UIKit's `maximumPossibleForce` for the Pencil,
  25/6: `p` = force ÷ 25/6. 1.0 is Notability's "unknown", so a curve whose
  forces are all 1.0 gets no `p`.
- **Paper colours**: legacy paper 0 (the 2014 tinted paper) is the paper
  menu's cream; `Color:` and template colours as stored. A template comes in
  as plain paper of its colour.
- **Stickies** are filled rectangle strokes in band 0, not empty text
  boxes: an empty box goes when its editor closes, the PDF writers leave it
  out, a box cannot turn, and the ink layer draws over boxes anyway. A
  picture or text box Notability draws over a sticky is therefore under it
  in Gamma, and so is a picture Notability draws over a text box; the
  report says so of each (`notability_map._covered`).
- **Sample spacing**: Notability's Bézier chains are flattened to samples at
  most a point apart (before the client's 0.1 pt decimation), within 0.05
  pt of the curve. The client's pen outline (perfect-freehand at streamline
  0.4) moves each point only part of the way to the next sample, so sparser
  samples would round a written corner.
- **Shape samples** are the web's own: a shape with a record is drawn as
  `shapes.js` draws its params (a line its two ends, a corner a sample and
  each side cut into steps of at most 2 pt, an ellipse max(48, perimeter ÷
  2) samples), since a handle drag writes the samples again from the
  params and a finer import would change at the first drag. Plain outlines
  take the same step. Against a 1 pt step, a note of every kind of shape
  stores half the samples and 40 % fewer ink bytes.
- **Shapes and curves**: on a page the stickies go first, then the curves
  and shapes in the note's z-order.
- **A partly erased shape** is a plain stroke per piece of its
  `outlinePath`, at the shape's width, so the eraser's gaps stay where
  they were; `strokePath`, the outline, would need a fill no stroke has.
- **No sync with recordings**: a recording becomes a recording block that
  plays, but its strokes carry no `t` or `t0`. Gamma's replay places a
  stroke by per-sample times ([dev/handwriting.md](../dev/handwriting.md)),
  never invented, and the event join is verified only for old versions.
- **A version 1 PDF** wider than 8.5 in is scaled down, which gnnote's
  sample supports.
- **A picture's crop** is kept as fractions of the image as a browser shows
  it: `FigureCropRectKey` over the file's pixel size (an `.ntb`'s over its
  logical asset size, mirrored first where the picture is flipped), and the
  whole image when there is none. The export multiplies it back by the
  file's pixel size. Inside the image a stretch is then the shown size's
  aspect against the crop's, with nothing to compute.
- **A crop past the image** is cut to it, and the frame is cut by the same
  fractions of the crop in its own axes, mirrored for the flips, its centre
  moved by the kept part's offset turned with it (`notability_map._cut`):
  the part kept shows where Notability drew it, unstretched. A crop larger
  than the image is the whole image in the part of the frame it covered; a
  crop holding nothing of the image is the whole image in the whole frame.
- **A small picture**: a frame with a side under 8 pt (`picture.MIN_SIDE`)
  grows about its centre until that side is 8 pt, its aspect kept; a page
  too small for it then scales it down (`picture.keep_on_surface`), the
  short side held at 8 pt.
- **A picture's turn** is kept in degrees, clockwise as seen:
  `rotationDegrees` holds radians, converted both ways. Every reader and
  writer applies the crop, then the flips, then the turn ("Pictures", above).
- **A GIF's animation** is the picture's file whenever the note has it,
  within the per-file cap and readable as a GIF: the browser animates it
  cropped, flipped or turned alike. Without it the still frame is shown.
- **Pictures stay as they came.** A PNG, JPEG, GIF or WebP is stored as it
  came and never decoded: only its pixel size is read, from its header,
  with the EXIF orientation applied where browsers apply it. A TIFF, BMP or
  AVIF, which a browser page cannot show, is stored as a PNG of its first
  frame, turned as its EXIF orientation says
  (`note_app_import.picture_file`), so the crop's fractions hold, and its
  grey kept to 16 bits; one past `pdf_image.MAX_DECODED_PIXELS`
  (40,000,000), in its pixels or in a TIFF's tile, is left out before it
  is decoded. HEIC, a picture of floating-point samples and any other
  format are left out with a warning. Each
  file of a note is read and stored once, however many pictures show it.
- **A caption** comes in as the content line by line: split at any line
  terminator (a line separator, U+2028, and a lone `\r` as well as `\n`),
  each line trimmed and escaped as plain text (`text_box.markdown_of`). It
  goes back as its plain text on one line.

## Still open

- Writing: whether the app opens what Gamma writes (only gnnote's ink has
  been tried), text boxes, PDF pages and pictures above all; what
  `staticWidth` does in the app (Gamma writes it for US Letter sheets);
  whether a blank page at the end of a note survives; a picture's turn,
  crop, flips and caption as Gamma writes them, and whether two pictures
  may share one file under `Images/` (Gamma writes two).
- Pictures: where the crop falls against the flips (no sample picture is
  both cropped and flipped); how pictures stack against ink and text boxes
  (Gamma draws every picture under both, and says so of one whose
  `zIndex` lies above a text box it meets; no sample shows Notability's
  order); how a caption is laid out on the page (Gamma keeps it in the
  notes).

- `.ntb`: a bundle that is an edit log, its edits, moves, deletes and
  restores read from the web app's schema with none to check them on;
  whether the iPad's NTB export writes a block's scale, caption switch and
  flips (5, 8, 16 and 17) and edits and moves (23 and 24) at all, since no
  sample has them; a page made by a later page record (3), which a
  position names by that record and its index, while the reader reads the
  index alone (the sample's records all name its first); recordings; shape
  fills and arrowheads; checklist ticks and text highlights; where several
  PDFs' pages go; text boxes and PDF pages, checked on written bundles
  alone.
- Strokes on recordings: the event join on current versions, and per-sample
  times interpolated over an event's duration.
- The flow text's layout on the page (it is imported as notes).
- Templates (a PDF behind every page), dot and grid rulings, line colours.
- The recognised handwriting (`HandwritingIndex`) as search text.
- How fountain pen, calligraphy and brush strokes are stored in `.note`
  (probably style 3 with a width profile); the meaning of style 6 and of the
  per-curve `options`.
- The rendering laws that are not stored: pencil shading, the highlighter's
  blend, dash lengths.
- Whether "Note" export exists on the Mac, Android and the web app.
