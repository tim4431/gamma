# What Notability and GoodNotes store per stroke, and why `gamma-ink` v2 looks the way it does

Survey 2026-10, for the one-time upgrade of Gamma's stroke file to
version 2 ([dev/handwriting.md](../dev/handwriting.md) "The stroke file").
Neither app documents its format; everything below comes from third-party
readers that were checked byte for byte against the apps' own output, from
class-dump headers of the Notability binary, and from the vendors' support
pages. Each claim is tagged **[verified]** when a converter reproduces the
app's own render or export from the stored bytes, or the vendor states it,
and **[inferred]** when it is a reading of names, numbers or community
reports. The earlier interaction survey is
[handwriting.md](handwriting.md), whose "File format" paragraph on
Notability follows this note.

## Notability (`.note`, `.ntb`)

Sources: the `gnnote` format document, the fullest spec with its own
verified/inferred tags, validated on 21 files
(https://github.com/jakubfabrici/notability-goodnotes/blob/main/docs/notability-format.md);
the `notesconverter` reader
(https://github.com/samuelsadok/notesconverter/blob/master/converter/notability.py);
`notability-to-pdf`, validated on 7,498 notes against Notability's own PDF
renders
(https://github.com/lh/notability-to-pdf/blob/main/docs/NATIVE_PAGES.md);
the `.ntb` analysis in `inkterop`
(https://github.com/cable729/inkterop/blob/main/docs/formats/notability.md)
and the Rust converter
(https://github.com/nokcha0/Notability-notes-converter#analysis-of-the-ntb-file-format);
class-dump headers
(https://github.com/itscrystalline/NotabilityShortcuts/tree/HEAD/NotabilityHeaders);
the engineering post on object selection
(https://gingerlabs.medium.com/notability-object-selection-adventures-in-vector-graphics-e1ed4bbbb4f6);
the first reverse engineering
(https://jvns.ca/blog/2018/03/31/reverse-engineering-notability-format/).
The whole `.note` file, beyond its strokes, and what the Notability import
reads of it are in [notability-format.md](notability-format.md).

- **One ink object per note, structure of arrays.** All handwriting of a
  note lives in one `InkedSpatialHash` inside `Session.plist`, as parallel
  little-endian arrays: `curvesnumpoints` (points per curve),
  `curvespoints` (float32 x,y pairs, all curves concatenated, absolute
  document coordinates), `curveswidth`, `curvescolors`,
  `curvesfractionalwidths`. **[verified]** by every reader above; 0
  violations of the array-length relations on 21 files (gnnote).
- **Curves are fitted cubic Bézier chains, not raw input.** Each curve has
  `1 + 3k` points (anchor, then control, control, anchor per segment; one
  point for a dot). Rendering `M a0 C c1 c2 a1 …` reproduces the app's own
  thumbnails; the control handles sit about a third of the way along the
  chord, a C1 smoothing fit. **[verified]** (gnnote, notability-to-pdf's
  0.961 mean ink IoU against the app's renders). No raw samples survive in
  the file. **[verified]** (no array holds them).
- **Width = one base width per curve × a fractional width per anchor.**
  `curveswidth` is the base width in document points (pens 0.37–3.7,
  highlighters 4.4–22 at a 565-wide page); `curvesfractionalwidths` holds
  one multiplier per Bézier anchor (`3F − 2N = P` holds exactly), observed
  0.27–2.13, median about 0.9, and a constant 1.0 when the pen's pressure
  option is off. **[verified]** (gnnote; notesconverter interpolates the
  multiplier linearly along each segment and matches the app's PDF
  export). How force becomes the multiplier (`forceToWidth:`,
  `velocityToWidth:`, `widthSensitivity`, a `widthsSmoothingArray` in
  the finger builder) is in the headers only: **[inferred]**, the mapping
  itself is not public.
- **Raw channels kept beside the baked width.** From 11.7.2 the arrays
  `curvesforces` (float32, 1.0 when unknown), `curvesaltitudeangles`
  (radians, π/2 default) and `curvesazimuthunitvector` (two float32, (1,0)
  default) store the Pencil's force and tilt per anchor, plus
  `curveUUIDs` (16 bytes per curve). **[verified]** in the 11.7.2, 15.3 and
  16.1.5 samples gnnote examined; which app builds wrote them first is
  **[inferred]**.
- **Tool is an explicit byte, not an alpha inference.** `curvesstyles`
  holds one byte per curve: 3 pen, 4 highlighter, 5 pencil (**[verified]**
  for 3 and 4 by colour and width; 5 **[inferred]** from opaque colours
  and 1.3–1.8 widths); the array is absent in ≤ 8.4.8 files and can be
  shorter than the curve count. The highlighter's colour alpha is stored
  as 0x6B (≈ 0.42) in 10.2.4, 15.3 and 16.1.5, 0x44 in 8.4.8.
  **[verified]**. The file does not order highlighters under pen ink
  (only `NBNoteTakingSessionIsHighlighterBehindTextKey`, behind typed
  text); converters that draw highlighters first then pens match
  screenshots. **[inferred]** for the on-screen order and blend mode.
- **Dash is a small enum, geometry derived.** `dashStyles` is a plist
  mapping a curve index to `pattern` 1 dashed / 2 dotted, solid curves
  absent; no dash or gap length is stored, so it is derived from the width
  at render time (the app's PDF export draws dotted highlighters as
  filled fragments). **[verified]** (notesconverter).
- **Shapes are parametric objects, not curves.** A recognised shape is a
  Shape record (`line`, `rectangle`, `square`, `triangle`, `polygon`,
  `circle`, `ellipse`, with an `arrowHead` and a dash style in its
  appearance), kept in z-order with its own uuid in 15.3+; a partially
  erased shape becomes `partialshape` with a CGPath outline that keeps the
  gap. **[verified]** (gnnote, nokcha0).
- **Partial erasing re-saves the stroke as separate curves.** No eraser
  masks are stored: the pieces are ordinary curves (still `1 + 3k`
  points) with the same style; the `.ntb` export encodes a break inside
  one ink path with a move-to command. **[verified]** (gnnote, inkterop).
  The cut geometry is not public.
- **The current format is an op log.** Modern `.ntb` (Mac 16.x, and the
  app's local persistence) is a zip with a FlatBuffers `noteBundle`: an
  append-only log of ops (document settings, text, ink type 15, shapes,
  images), each ink op carrying origin, tool byte, RGBA, base width and a
  point blob with a per-segment f16 width multiplier and an opacity byte,
  with pen-down and pen-up millisecond timestamps. **[verified]** framing
  and the fields named here (inkterop, nokcha0); several constants are
  unknown. Synced through CloudKit without a server. **[inferred]** from
  the architecture (no source states the transport).
- **Audio sync is per stroke.** One event (start time, duration,
  recording id) per curve, joined by an event token or, from 11.7.2, by
  `curveUUIDs`. **[verified]** (gnnote, note-core-swift).
- **Patents.** The only Ginger Labs family is US11677708B2 (collaboration
  messages); nothing on ink, erasing or shapes
  (https://patents.google.com/?assignee=%22Ginger+Labs%22). **[verified]**
  (negative search result).

## GoodNotes (`.goodnotes`, schema 24 / 25 / 35)

Sources: the byte-exact container, stroke, element and schema-35
documents in the same repository
(https://raw.githubusercontent.com/jakubfabrici/notability-goodnotes/main/docs/goodnotes-container.md,
https://raw.githubusercontent.com/jakubfabrici/notability-goodnotes/main/docs/goodnotes-stroke.md,
https://raw.githubusercontent.com/jakubfabrici/notability-goodnotes/main/docs/goodnotes-v35-strokes.md,
https://raw.githubusercontent.com/jakubfabrici/notability-goodnotes/main/docs/goodnotes-v35-elements.md,
https://raw.githubusercontent.com/jakubfabrici/notability-goodnotes/main/docs/goodnotes-elements.md,
https://raw.githubusercontent.com/jakubfabrici/notability-goodnotes/main/docs/inkterop-goodnotes.md);
`goodnotes-codec`, a lossless re-encode of 307,070 stroke blobs across
four schemas; `goodparse` (https://github.com/franzthiemann/goodparse);
the pen-tool support page
(https://support.goodnotes.com/hc/en-us/articles/7353756785679-Write-and-customize-ink-with-the-Pen-tool);
the highlighter and eraser pages
(https://support.goodnotes.com/hc/en-us/articles/7353718215823-Using-the-Highlighter-tool,
https://support.goodnotes.com/hc/en-us/articles/7353718249231-Erasing-content);
the Swift-on-web engineering post
(https://www.swift.org/blog/bringing-goodnotes-to-web-with-swift/).

- **A record stream per page plus an event log.** A `.goodnotes` file is
  a zip: `index.events.pb` (an event log), `notes/<UUID>` one file per
  page holding `<varint length><protobuf record>` pairs, one metadata
  record (uuid, clock, tombstone flag, device id, per-device counter) and
  one content record per element; schema 25 pairs them strictly as a
  journal. **[verified]** (gnnote; goodnotes-codec's lossless round trip
  of 307,070 strokes in 206 documents).
- **A per-stroke clock for last-writer-wins.** Every mutable value is a
  `{version, nonce}` register (version +1 per edit, moved strokes reach
  10–17; a random uint32 nonce), and the event log holds a per-page
  version vector of `{stroke uuid, {version, nonce}}`. **[verified]** (91
  of 91 strokes matched on a calibration file). GoodNotes engineers
  describe sync as "CRDT + event sourcing". **[inferred]** from public
  talks quoted by the readers, not a vendor document.
- **Erased strokes are removed from the page file**, not tombstoned in
  place: every live record renders in the app's own PDF export.
  **[verified]**.
- **Width is the final rendered value, per point.** Flat strokes (ball
  pen, highlighter, shapes) are quadratic Bézier chains with one nominal
  width `W` (on-page width exactly `W/2` pt: 24 → 12 pt, 1.559 → 0.78 pt).
  Ribbon strokes (fountain pen, brush pen, marker) store `(x, y, r)` per
  point where `r` is the half-width radius with pressure and velocity
  already baked in, plus a pre-built outline (move-to, cubic, arc
  commands) that the app fills with nonzero winding. **[verified]**
  (gnnote; the parser authors report 99.79 % raster agreement on
  eraser-cut outlines). No raw pressure is stored for pens; fountain and
  brush are not distinguishable per stroke; the pressure → radius curve
  cannot be read from files. **[verified]** as absences.
- **The pencil stores raw channels and is rasterised on export.** Pencil
  records (`#3 = 5`, schema ≥ 25) carry per point `(x, y, altitude-like,
  azimuth-like, force)` and a random u32 grain seed per segment; the app
  exports pencil strokes as raster images, and two strokes with identical
  records were seen rendered at widths 2.4× apart, so a brush setting lives
  outside the record. **[verified]** for the fields and the raster export;
  which angle is altitude and which azimuth, **[inferred]** from ranges.
- **Highlighter is a flag with a fixed alpha.** An ordinary flat stroke
  with `#5 = 1` and colour alpha exactly 0.5 (62/62 and 12/12 cases in two
  corpora), round caps and joins, exported as one path per stroke with
  `CA 0.5`. **[verified]**. The file has no layer field: draw order is the
  per-element counter, and renderers that draw highlighters first then
  pens match the app's screenshots. **[inferred]**.
- **Recognised shapes are parametric records** beside an empty geometry
  blob: a point list (line, polyline), one quadratic curve, an
  axis-aligned rectangle `{centre, (w, h)}`, a rotated ellipse `{centre,
  semi-axes, θ}`, with a nominal width; a closed shape's fill is a
  separate record pointing at the outline stroke with alpha 0.1.
  **[verified]** (gnnote on schema-35 files).
- **A partially erased stroke stays one record with sub-paths** (a flag
  of 0 starts a sub-path), so its identity and time base survive the cut.
  **[verified]** on a legacy-format record with two sub-paths; schema-35
  cuts **[inferred]** to behave the same.
- **No per-point time.** Strokes carry no timestamps; replay timing for
  audio lives in the recording's events, per stroke
  (https://github.com/Kaih1825/parser-for-goodnotes/wiki/09-Current-RE-Findings).
  **[verified]** for the stroke; the 31.25 kHz event clock **[inferred]**.
- **Preset settings are not in the stroke.** Pressure sensitivity (fountain
  and brush pens, default 50 %, never fully off), tip sharpness, dash
  pattern with density from the thickness slider, stabilisation and
  "reduce latency" (iOS predicted touches) are per preset in the UI
  (support page, **[verified]** as product facts); none of them is stored
  in the stroke record. **[verified]** as an absence (goodnotes-codec's
  field inventory).
- **A shared in-house engine.** The ink renderer is Swift on Metal and
  WebGL with the rendering logic "almost entirely shared" across iOS and
  web. **[verified]** (vendor engineering post).

## What both apps do not publish

The force → width mapping of any pen, the width smoothing window, the
pencil's tilt and opacity formulas, the hold time and jitter radius of
draw-and-hold, the shape classifiers' thresholds, the partial eraser's cut
geometry, the on-screen highlighter blend mode and draw order, dash and
dot spacing, and the selection tolerance. Converters approximate the
pencil (`0.38 + 0.30 √p` for width, `0.08 + 0.18 p` for alpha, nokcha0)
and every reader assumes multiply for highlighters. Whether either app's
*own* reader tolerates unknown fields is not stated by any source;
third-party protobuf readers skip unknown fields, and the Notability
document warns writers not to rely on missing arrays being tolerated.

## Why `gamma-ink` v2 looks the way it does

- **Baked width as a multiplier of a base size, raw channels kept.**
  Notability's `curveswidth × curvesfractionalwidths` with `curvesforces`
  and the tilt arrays beside it is the shape v2 copies: `size` is the
  base, the `w` channel the per-sample multiplier (× 1000, delta-coded),
  and `p`, `a`, `z` stay raw. GoodNotes stores the absolute radius
  instead; the multiplier was chosen because a width restyle then touches
  `size` alone and the samples' bytes stay identical, which is what
  content-addressed uploads and the merge want. Baking once on the client
  (`finishStroke.bakeWidths`) is what both apps do: the file holds what
  was drawn, and the server, the SVG, both PDFs and the AI picture read
  it rather than re-deriving it. GoodNotes' pencil is the exception: it
  keeps raw channels because its look is a shader. v2 bakes the pencil
  too, its shade per sample in the `o` channel beside `w`.
- **An explicit tool and band, not an alpha inference.** Both apps store
  the highlighter as a flag or a style byte with a fixed alpha and leave
  the draw order to the renderer; v2 keeps `tool` and adds `band` (omitted
  at the tool's default) so "highlighter behind ink" is a stored fact
  every renderer agrees on, and a tap prefers the ink band.
- **Open tool and brush strings** instead of enums: Notability's style
  byte gained a value (pencil) and GoodNotes' geometry version gained
  families (ribbon, pencil) without a container change; v2 lets a later
  brush be a new value drawn plainly from `w` by a copy that does not
  know it, with the server keeping unknown keys verbatim under byte caps.
- **Parametric shapes beside the baked polyline.** GoodNotes' shape
  records and Notability's Shape objects keep a shape editable; v2's
  `shape {kind, params}` does the same, but the stroke also carries its
  `pts`, so every existing consumer (hit test, eraser, replay, exporters,
  older copies) needs no shape code.
- **Pieces that know their parent.** Both apps re-save a cut stroke
  (Notability as new curves, GoodNotes as sub-paths of one record). Gamma
  re-saves as new strokes, like Notability, because its merge and undo are
  keyed by stroke id; `from` gives the merge what GoodNotes gets from a
  single record, so a cut against a restyle composes and a cut against an
  undo leaves nothing behind.
- **Merge by stroke id, not a clock.** GoodNotes' `{version, nonce}`
  register exists to sync through CloudKit with no server; Gamma has a
  server three-way merge by stroke id already, so v2 keeps it and splits a
  stroke into geometry and look so two writers' edits compose where
  per-stroke rows with a geometry and a style version would have.
- **One file per group, not an op log or per-stroke rows.** Notability's
  `.ntb` log and GoodNotes' record stream are their sync transport; Gamma
  transports pages, trees and files, and every existing path (orphan
  cleanup, share scoping, quota, bundlers, backups) understands an upload
  named by its hash. The one-time upgrade to v2 rewrites those files
  deterministically so every copy converges on the same hashes.
- **Declined.** Fitted Bézier chains (Gamma keeps raw samples for replay,
  re-baking and audio sync, which Notability's fit discards and GoodNotes
  never had), a stored outline (GoodNotes caches tessellation; perfect-
  freehand is cheap enough and a cache would be a second truth), a stored
  dash geometry (both apps derive it from width), a stored eraser mask
  (neither app has one).
