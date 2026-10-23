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
- **`.ntb`**, Notability Cloud's format and the Mac app's Save As, is
  detected and refused with how to export "Note" instead (below, ".ntb").
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
  (full resolution), else `contentSnapshot.relativePath` (a smaller copy).
  `imageIsMissing` means Notability lost the file [V].
- `FigureCropRectKey` is the crop in source pixels, the whole image when
  uncropped; `unscaledContentSize` the shown size after the crop and before
  the turn; `isFlippedHorizontal`/`Vertical` from 15.3 [V]. The order of
  the flips and the turn is not documented [I: flip, then turn].
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
  styles, ink (type 15), shapes (18), media (22). An ink op carries its
  page, origin, turn, scale, tool, dash, RGBA, base width and a point blob
  of offsets from the origin with two float16 values and an opacity byte per
  anchor.

**Verdict: not imported.** Fresh strokes decode reliably, partial erases
too. Not established: what the op log keeps for a stroke deleted, undone,
moved or restyled (inkterop's erase audit leaves it open, and nothing in a
file says whether such edits happened, so an importer would bring back
erased ink in an unknown share of notes); the meaning of the two width
values; PDF placement beyond one PDF in order; text-box ownership edge cases;
audio; and the schema's stability (its one version marker is a constant).
Since then nokcha0's converter reads type 25 events as lists of erased
records and leaves those out, and turns its example's `.ntb` into the
strokes, shapes, groups, pictures and text of the same note's "Note"
export [V1]; deletes, undos, moves and restyles in general stay unmapped.
Gamma refuses an `.ntb` (400) with how to export "Note" instead: Export
Options → File Format "Note" (in a note: the options icon; in the library:
press and hold the note). Third-Party Backup offers only NTB, PDF and PDF +
Recording; 16.1.5 on the iPad still writes `.note`.

## What Gamma decided where the sources conflict or say nothing

- **A static-width letter page's height** is floored to a whole unit, as
  gnnote and nokcha0 read `.note`; the `.ntb` exports' 789.41 differs by 0.4
  units a page.
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
  out, a box cannot turn, and the ink layer draws over boxes anyway.
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

## Still open

- `.ntb`: the op log's deletes, undos, moves and restyles; the width values;
  several PDFs; audio.
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
