# Handwriting (ink) annotation: formats, input, and what the fork did

For the broader interaction survey—finger selection, contextual edit menus,
transforms, correction gestures and study tools—see
[handwriting-interactions.md](handwriting-interactions.md). That follow-up
distinguishes current documented behavior and platform limits from the
historical format/input findings below.

Survey from 2026-09-13, before the handwriting feature described in
[dev/handwriting.md](../dev/handwriting.md) was designed. Three questions:
which ink formats exist and which are worth speaking, what stylus input the
browser gives on each platform, and what the upstream fork's iPad-based
implementation taught. Findings only; the chosen mechanics live in the dev
doc.

## Ink formats in the wild

| Format | Who writes it | Shape | Pressure / tilt / time | Verdict for Gamma |
|---|---|---|---|---|
| **InkML** (W3C Recommendation, 2011) | Handwriting-recognition toolchains, some Microsoft/Samsung pipelines; no consumer note app | XML: `<trace>` = one stroke as a list of samples; `<traceFormat>` declares the *channels* per sample (`X Y F T`, tilt `OTx OTy`, colour, width); `<brush>`, `<context>`, `<traceGroup>` | All, as declared channels | Nothing produces or consumes it in a workflow Gamma's users have. Its one durable idea, "a stroke is samples over declared channels", is copied into the Gamma schema. Not an interchange target. |
| **Xournal++ `.xopp`** | Xournal++ (Linux/Windows/macOS), the open-source PDF annotator people actually use with pen tablets | gzipped XML document: pages with a `<background type="pdf" filename= page=>`, `<layer>`s, `<stroke tool= color="#RRGGBBAA" width="w0 w1 w2 …">x y x y …</stroke>`; pressure is stored as *per-point widths* (first value = base width), `capStyle`, dash `style`, `fill`, audio `ts`/`fn` | Pressure as widths; time only as an audio offset | Worth an **importer** (people migrate from it, and it is the only open document-level format with a PDF background). Not a storage model: it is a whole notebook, not a block. |
| **PDF `/Ink` annotation** (ISO 32000) | Every PDF app: Acrobat, PDF Expert, Xournal++ export, Zotero 7's reader (ink tool + import), GoodNotes/Notability when exporting with annotations | `/InkList [[x y x y …] …]` polylines in PDF user space, `/BS /W` constant width, `/C` colour, `/CA` opacity, `/Contents` note; private keys allowed | None (constant width) | **The interchange format.** Export to it (Gamma already writes `/Highlight` and `/Square`), import from it (since built: `pdf_export.py` writes `/Ink`, and a PDF's own `/Ink` annotations import as ink groups, `ink.py`). Pressure is lost unless the writer stashes its own data in a private key. |
| **tldraw draw shape** | tldraw SDK, Logseq whiteboards | JSON record: `segments[{type: free|straight, path: delta-encoded base64 of (x,y,z)}]`, `isPen`, `isClosed`, style enums (`color`, `size`, `dash`, `fill`), `scale` | z = pressure; no time | Good engineering reference: compact delta encoding, a "real pen vs simulated pressure" flag, record-level collaboration (property LWW) that matches Gamma's op model. Its style enums are tldraw's; not a file people exchange. |
| **Excalidraw `freedraw` element** | Excalidraw, the Obsidian Excalidraw plugin | JSON element: `points[[x,y]]`, `pressures[]`, `simulatePressure`, `strokeOptions{variability, streamline}`, `strokeColor`, `strokeWidth`, `opacity` | Pressure; no time | Same lesson as tldraw: keep raw points + pressure, render with perfect-freehand, never store the outline. Whole-scene file, not a target. |
| **reMarkable `.rm` v6** | reMarkable tablets; parsers `rmscene`, `rmc` | Binary CRDT scene tree; `Line{tool, color, thickness_scale, points[{x, y, speed, direction, width, pressure}]}`, 24 pen variants | Pressure, tilt-derived width, speed | Niche importer at most (via `rmscene`). Shows what a *rich* per-point channel set looks like when a device owns the pen. |
| **Apple PencilKit `PKDrawing`** | iPadOS apps | Opaque "Apple Drawing Format" bytes; the API exposes `PKStroke{path: [PKStrokePoint{location, timeOffset, size, opacity, force, azimuth, altitude}], ink, transform}` but the serialisation is undocumented and version-gated (iOS 17 inks fail to decode on older OS) | All, but only inside Apple code | Never as a source of truth: cannot be read on the server, in a browser, or on any other device. The fork's whole derived-asset pipeline (PNG previews, per-stroke PNGs) exists only because of this. |
| MyScript JIIX, OneNote, Notability, GoodNotes | Proprietary | JSON (JIIX) or closed | n/a | No public reader; ignore. |

**Conclusion.** No open format is both a good *storage* unit for a block and
something other software exchanges. The two roles split cleanly: store raw
samples in Gamma's own small JSON schema (borrowing InkML's channel idea and
Excalidraw/tldraw's raw-points-plus-pressure rule), and exchange through PDF
`/Ink` (universal), SVG (rendering), and a `.xopp` importer (migration).

## Rendering pressure strokes

**perfect-freehand** (MIT, ~2 kB, by tldraw's author) turns `[x, y, pressure]`
samples into a closed outline polygon; options `size`, `thinning`,
`smoothing`, `streamline`, `simulatePressure`, start/end taper. Excalidraw,
tldraw and most web ink apps use it. Its README's rule is the storage rule:
*store input points, never the outline*, so strokes stay editable and the
renderer can improve. The outline is one SVG `<path>` (or a canvas `Path2D`)
per stroke; page-unit coordinates plus a CSS `transform: scale()` make zoom
free, exactly how pdf.js's text layer is scaled in Gamma's viewer already.

pdf.js ships its own `InkEditor` (`AnnotationEditorType.INK`), but it lives
inside pdf.js's `PDFViewer` / annotation-editor layer, which Gamma's custom
viewer does not use; its serialisation targets `/Ink` (constant width). It is
a reference for the pointer plumbing, not a component to embed.

### Where perfect-freehand's outline bends a stroke (survey 2026-10-29)

Surveyed when the Sketchbook's drawn toolbar icons (1.1 pt Ballpoint lines
tracing the app's 24-unit icon paths at 0.47 pt a unit) came out with dented
corners, and a Notability import's handwriting and shapes showed the same.
perfect-freehand 1.2.2, read from its source (`getStrokePoints`,
`getStrokeOutlinePoints`), and measured on the Sketchbook's four sheets and
the Notability and GoodNotes samples in `backend/tests/samples/`:

- **Three absolute constants.** Everything else in the library scales with
  `size` (the minimum spacing of outline points, `(size × smoothing)²`; the
  start drop; the streamline's step, a fraction; the sharp-corner test, the
  sign of a dot product), so outlining at ten times the page's scale and
  dividing back changes exactly these three, each to a tenth:
  - The outline skips every input point whose remaining running length is
    under 3 units and joins the last kept point straight to the end. In page
    units that was the last 3 pt of every stroke: a 7-unit lens circle
    (3.3 pt radius) lost a 53° arc to a chord, the fit-width icon's 4 pt
    chevrons collapsed into bars, the tail of a lettered "S" became a
    straight stub with a blob, and a stroke under 3 pt kept only its ends.
  - A lone input point gets a second point at (1, 1) units, so a stroke of
    one sample drew as an off-centre blob about 1.4 pt plus the pen's width
    across (6.35 pt at size 5, where the pen is 5 pt). At ten times the
    scale it is a round dot of the pen's width, as the exporters draw it.
  - The radius is floored at 0.01 units, invisible either way.
- Dashes and pencil shades were already outlined at ten times the scale
  (`runOutline`, the server's `stroke_outline`); whole strokes now are too
  (`penOutline`, `pen_outline`).
- **The start.** `getStrokePoints` skips samples while the distances of
  their streamlined points from the start add up to less than `size`, and
  joins the first sample straight to the first point it keeps, which lies
  0.66 of the way to the sample that produced it. With the straight-run
  cut, on the Notability sample's strokes that point lies 0.52 × size along
  the samples at the median and at most 2.2 × size (0.84 and 2.8 before
  the cut: denser samples shorten the drop, since each skipped one adds
  less). It scales with the size and hides the tiny hooks a stylus leaves
  at pen-down (the largest gap it leaves against an outline without it is
  0.65 of the line's width, 0.19 pt), so it was left as it is. It is also
  why a Brush mark shorter than its size drew only its two tapered ends,
  both at the floor: such a mark is no longer tapered (below).
- **Corners: the streamline's lag.** With `streamline` 0.4 each point moves
  0.66 of the way from the previous output point to its sample (`0.15 +
  (1 − streamline) × 0.85`), so the line trails about 0.515 of a sample gap
  behind and cuts every corner by that much: about 0.4 pt on handwriting
  sampled 0.8 pt apart, which reads as a round join, but about 1 pt on
  shapes sampled every 2 pt (shapes.js `SAMPLE_PT`, `ink.SHAPE_STEP`, the
  Notability and GoodNotes shapes, the Sketchbook's lettering), whose
  vertices fell outside the ink, and more on drawings handed over as bare
  corners (the Sketchbook's star, 14 pt between tips, and its pressed
  grounds). The quadratics through the outline's midpoints round it a
  little further.
  - Resampling every run to 0.25 pt removes it (on the Notability sample,
    spots off the ideal line by half its width or more: 295 to 3), but it
    also removes the streamline's smoothing where a fast hand's samples are
    4 pt apart: a quick circle drew as a polygon. Declined.
  - Built instead: only straight runs are cut finer (`denseStraights`,
    `_dense_straights`). A straight run has nothing to smooth; dense
    samples there carry the line into its corner, and a curve keeps its
    smoothing. Measured, the same spots: Notability 395 on dev, 295 after
    the ten-times scale, 5 with straight runs; GoodNotes 14 to 2; the
    Sketchbook's sheets 31, 40, 40 and 50 (at the ten-times scale) to 0,
    once the Sketchbook samples its star, arrowheads and pressed grounds
    along their sides (`_along`).
  - A first version cut a segment wherever the stroke turned by less than
    3° at either of its ends, along its whole length. A review found two
    faults. A lone gentle turn at the inflection of a fast, sparse curve
    (samples 4 to 7 pt apart) drew the segment after it as a straight chord
    with corners at both ends; on test sines 104 and 407 such kinks. And
    the cost grew with length, about four samples a point, so one crafted
    stroke spanning the largest coordinates could stall an export or a
    tab. Now a sample is straight only when a neighbour is too or it is
    next to an end (kinks 104 to 7 and 407 to 21, the census still 5), and
    a cut segment gets only 24 quarter-point steps at each end: the
    streamline closes 0.66 of what is left at every step, so 24 steps
    draw in even a jump of the largest page's span, and a 1000 pt line
    of two samples gets 99 samples instead of 4000.
  - It still costs: a page of imported handwriting outlines in 1.6 to 2.5
    times the time with paths 19 to 43 % longer, once per stroke (the
    outline is cached); 70 % of the Notability sample's freehand segments
    are cut, its near-straight stretches. Jittery, nearly straight mouse
    lines follow their jitter a little more (worst 0.54 to 0.71 pt off the
    true line), not visible at 10 to 24 times.
  - What is left is under the line's width: corners up to about 120° are
    bevelled by the outline (0.29 of the radius at 90°), and arcs smaller
    than half the line's width become chords (the minimum outline spacing),
    so the Play icon's tip, the More icon's dots and the small loops of the
    lasso stay a little small. The lettering's bowls and the icons' small
    circles, sampled every 2 pt and 0.8 pt, still shrink by up to 6.5 % (19
    % for the lasso's 0.9 pt loop); sampling them by turning angle would
    fix it at the cost of about 40 KB a sheet.
- **A regression the ten-times scale exposed.** At the page's scale a
  two-sample stroke drew perfect-freehand's own in-between points at
  pressure 0.5, whatever its `w`. With its midpoint lerped, it draws its
  baked widths, which is right for every brush but the Brush: its tapers
  put both samples of a two-sample mark at the 0.1 floor (a quick flick, or
  any mark under Smoothing shorter than half its string, which commits only
  its two ends), so it became a hairline where it had been a teardrop. A
  finished two-sample Brush mark is no longer tapered, nor one shorter than
  its size, whose middle the start drop skips (a dot written with a little
  travel had become a 0.2 pt speck) (`finishStroke.brushWidths`, the
  iPad's `InkWidth.widths`, `tests/shared/inkwidth.json`); one baked before
  keeps its floors until it is restyled. A Smoothing mark between half the
  string and the string's length still commits three samples, the middle
  one a quarter of the way along, and peaks at about 0.7 of the width;
  spacing the stabilizer's catch-up evenly would fix it.
- **Parity.** The server's port matched the screen number for number on
  4 800 fuzzed pencil and translucent strokes and every stroke of the
  samples, and `denseStraights` and `_dense_straights` agreed to the bit on
  36 300 runs, the 3° threshold included. Two older differences in how a
  pencil is cut into bands showed up once the step counts made them
  visible, and were fixed: the client copied a sample when its `o` sat
  exactly on a band's edge (the server did not), and the server summed arc
  lengths with Python's `math.hypot`, which differs from V8's `Math.hypot`
  by up to 2 ulp on about a third of inputs (now `_js_hypot`, as the
  shapes already were). Left: `_pf_points` still measures with
  `math.hypot`, so a running length that ties the start drop's threshold
  to the last bit keeps a point on one side only (a contrived 0.15 pt
  pencil stroke); and where `o` touches an edge at a sample and turns
  back, the server's float cut can leave a run of almost no length that it
  draws as a dot the screen leaves out.
- **How it was measured.** The sheets and the imported samples were drawn
  through `strokePath` from dev and from the branch into SVG and
  screenshotted with headless Chrome at 3× to 48×, against the icons' own
  SVG stroked with round joins and against each stroke's samples drawn as
  a round-capped polyline at its width; the census counts the spots where
  the outline strays from that ideal by at least half the line's width.

### Why Gamma's ink looks smoother than Zotero's (survey 2026-10-29)

Asked when a page synced to Zotero showed its handwriting less smooth in
Zotero's reader than in Gamma. Read from zotero/reader at 293d7e1
(`src/pdf/lib/render.js`, `src/pdf/page.js` `_pushInk`,
`src/pdf/lib/path.js`, `src/pdf/pdf-view.js`) and zotero/pdf-worker
(`src/pdf/annotations/read.js`), against Gamma's `/Ink` writer
(`gamma/pdf_export.py` `_ink_annotations`):

- **What Zotero receives.** Gamma writes each look bucket (tool, colour,
  size, opacity) as one `/Ink`: `/InkList` is every stroke's stored samples
  as they are, `/BS /W` the mean drawn width of the whole bucket. The
  pdf-worker reads `/InkList` and `/BS /W` only, rounded to a thousandth;
  it ignores `/BS /S /D` and any `/AP`, so a dashed pen arrives solid and
  an appearance stream would not reach it.
- **How Zotero draws it.** Straight `lineTo` segments between the points,
  one `lineWidth`, round caps and joins. Its own smoothing (two Chaikin
  passes, then points closer than 1 unit dropped) runs only on a stroke
  drawn in its reader, when it is finished; imported ink is never
  smoothed.
- **How Gamma draws the same samples.** The preset's Smoothing (the
  stabilizer) and the 0.1 pt decimation are baked into the samples, so
  Zotero has them too. What Zotero lacks is the renderer's: perfect-
  freehand's streamline (each point moved 0.66 of the way to the next
  sample, a lag of about half the sample spacing, about 0.4 pt on a
  Notability import whose samples are 0.82 pt apart), outline points closer
  than half the width dropped, the outline drawn as quadratic curves
  through its points' midpoints, and each sample's own width.
- **Measured** on the Notability example note's first page (531 strokes,
  22 492 samples, spacing median 0.82 pt, 99th percentile 1.99 pt, longest
  27 pt): at 6 to 10 times, Zotero's rendering shows the polyline's kinks at
  every turn, keeps the corners of drawn shapes sharp where Gamma rounds
  them, draws a pressure line at one width, and draws dotted and dashed
  pens solid.
- **What would bring Zotero closer.** Writing the renderer's smoothed
  centreline into `/InkList` (the streamline applied and each curve
  subdivided) while `/GammaInk` keeps the raw samples for a Gamma
  re-import; and one `/Ink` per stroke, so each carries its own mean width.
  A width that varies along a stroke cannot reach Zotero, whose ink has one
  width.

## Stylus input per platform (browser)

The W3C Pointer Events API is the one input path that covers every platform
Gamma runs on: Chrome/Edge/Firefox on Windows/Linux/macOS (Wacom, Surface
Pen, Huion), Electron (the desktop app), Android Chrome, and Safari on
iPadOS with Apple Pencil.

- `pointerType` = `pen` | `touch` | `mouse`; `pressure` (0..1), `tiltX/Y`,
  `twist`; `buttons & 32` is the eraser end (Surface/Wacom), `buttons & 2`
  the barrel button. `setPointerCapture` keeps a stroke alive off the
  element.
- `getCoalescedEvents()` yields the full sample rate between frames on
  Chromium and Firefox. Safari does not implement it but delivers
  `pointermove` at the Pencil's native 120/240 Hz, so the two paths give
  comparable density. `getPredictedEvents()` is Chromium-only; it is a
  latency nicety, not a requirement.
- Safari + Pencil: pressure and tilt are real; touch and pen arrive as
  separate pointer types, so "pen draws, finger scrolls" is a one-line
  rule. Setting `touch-action: none` only on the ink layer while a tool is
  armed keeps the viewer's pinch-zoom working otherwise.
- Latency: draw the in-progress stroke on a `<canvas>` with
  `{desynchronized: true}` and commit to the retained layer on pointer-up.
  Chromium's low-latency canvas path is what browser ink apps (Excalidraw,
  tldraw, Microsoft Whiteboard web) use.

So a browser ink layer is *the* multi-platform implementation. A native
client is not needed for pen quality; the fork built one because it wanted
PencilKit's look and audio replay, and paid for it with an opaque format.

## What the upstream fork built (amogadget/Gamma v0.4.0)

`docs/design/handwriting-recording-ipad.md` there describes a native iPad
app (PDFKit + PencilKit + AVFAudio) that logs into the Gamma server, embeds
the web workspace in a WKWebView, and syncs ink as blocks. The web side only
*displays* ink. The design went through three shapes (standalone `.note`
bundles, then Gamma-native blocks, then a hybrid app with browser replay),
which is itself a finding: keeping ink outside the block tree created a
second document identity and was abandoned.

Worth adopting:

- **One block per ink group, not per stroke.** A derivation in a margin is
  dozens of strokes; per-stroke blocks wreck the outliner and multiply ops.
  The user starts a new group explicitly ("New Ink"); strokes inside are
  drawing data.
- **Ink is a block**: it sits in the notes tree under the page, its
  `content` is the caption/transcription (what search and AI see), it can
  have children. Text edits and drawing edits must not clobber each other.
- **Canonical page coordinates** independent of zoom, scroll, device pixels
  and rotation, converted at the boundary with an affine map. They used
  unrotated crop-box points; Gamma's existing `pdf_position` convention is
  the displayed page at scale 1 and the PDF writers already map that to
  user space, so the plan keeps *that* rather than adding a second
  convention.
- **Never rewrite the PDF**; ink is an overlay, export is a separate file.
- **One editable source, no derived display asset that can go stale.** They
  learned this the hard way (blurry whole-block PNG, then per-stroke PNGs,
  then budgets and backfill endpoints). With an open vector format there is
  nothing derived to keep in sync.
- **Sidebar marker + jump + brief flash** for locating a group on the page.
- **Per-point time offsets** cost nothing to store and are what makes
  audio-synchronised replay possible later. Their rule "never fabricate a
  time you did not record" is right.

Not adopting, and why:

- PKDrawing as source of truth, PNG previews, per-stroke PNG `.inkjson`
  replay derivatives, the 32 MiB asset caps built around them: all
  consequences of an opaque format.
- A separate endpoint family (`PUT /blocks/{id}/ink|note|audio|replay-preview`
  with `expected_revision`, lost-response retry rules, an outbox) that wrote
  `unified_blocks` rows with raw SQL. This repo's `apply_ops` + op log + page
  socket already give idempotent inserts, property-level patches, ordering
  and live fan-out; a raw-SQL side channel would re-open exactly the
  problems [collaboration.md](collaboration.md) closed.
- `native_note` ancestry validation and "parent must be a PDF page" checks:
  the block-centric direction wants ink on *any* block, not only under a
  PDF page.
- Audio recording/replay: a separate feature; the format leaves the door
  open.

## How Notability sets up writing (survey 2026-09-14)

Notability (Ginger Labs, iPad) is the pen experience Gamma's handwriting
is measured against. Facts below are from Ginger Labs' support articles
unless marked *community* (reviews, reverse-engineering write-ups); exact
point sizes and the default palette's hex values are not published
anywhere found.

**Pen.** Two ink styles: *ballpoint* (constant width) and *fountain*
(width follows pressure), each also as dashed or dotted; pressure is a
toggle in the pen popover ("~"). Widths are 12 fixed sizes per tool, of
which the toolbar shows three slots at a time (keys Ctrl+Cmd+1..3). Colours:
32 defaults plus custom colours (wheel, hex, dropper) in 64 slots, with 8
"fast" custom colours in the toolbar row (Cmd+Shift+1..8). The separate
*Pencil* tool shades: tilt widens, pressure darkens (opacity), still
vector. A *Calligraphy* pen has a flat nib with an angle slider and a
"Stabilization" toggle that smooths jitter. Ink is vector; hit-testing is
distance-to-path (their engineering blog).

**Highlighter.** Translucent, same popover as the pen (colours, 12 sizes,
pressure/dashed/dotted). It renders *behind* the ink (*community*), so pen
strokes keep their colour on top of it. Draw over text and hold to get a
straight line; a long-press on PDF or typed text snaps the highlight to
words (Smart Highlighter), handwriting is never snapped.

**Eraser.** Two modes, *Partial* (the cut segments become independent
strokes with their own width and colour) and *Whole*, plus 12 sizes, all in
the eraser popover. Partial + hold snaps the erased path to a shape. A
setting "Auto-Deselect Eraser" returns to the previous tool after an erase.
Apple Pencil double-tap (gen 2) is configurable: current ↔ eraser, current
↔ last tool, colour palette, ink attributes, or off; Pencil Pro squeeze
adds hold-to-erase. There is no "back of the pencil" eraser.

**Input.** Palm Detection is a setting; with a Pencil active, one finger
scrolls and a toolbar button "disconnects" the Pencil so fingers draw
until the next Pencil touch. Nothing is published on velocity-based width,
min/max ratio or predicted touches.

**Lasso and shapes.** The Select tool is *freeform* or *boxed*; the
selection moves by drag, scales by pinch or handles, rotates by twist; a
tap inside opens Style (change pen type / width / colour / dash of existing
ink), Convert (to text or math), Copy, Cut, Duplicate, Group, Save as
sticker, Delete. Shapes: draw and hold about a second → circle, ellipse,
triangle, square, rectangle, pentagon, hexagon, arrow, with snapping
guides, editable vertices and stroke + fill styling; "Straight Lines" and
"Shapes Detection" are settings. A Ruler stamps lines and snaps nearby
strokes.

**Toolbar.** One movable strip, default order Pen, Pencil, Highlighter,
Eraser, Text, Select, Media, Audio, Hand, Zoom, Tape, Ruler, Laser; tools
can be hidden, reordered and *duplicated* as preset copies (a second pen
with its own colour and size, Cmd+1..9 by position). Each tool's popover
is one colour row (8 fast + palette), one size row (3 of 12) and the style
row.

**File format** (*community*: `.note` is a zip with an NSKeyedArchiver
`Session.plist`). Strokes are flat float32 point arrays with per-stroke
point counts, one base width and one RGBA colour per stroke (alpha carries
the highlighter's translucency), and per-anchor *fractional widths* (the
pressure record, a multiplier of the base width). The points are a fitted
cubic Bézier chain (`1 + 3k` points per curve: anchor, then control,
control, anchor), not a polyline, and the careful converters interpolate
the fractional widths along each segment; the first ones drew `M/L` paths
and ignored them. What this and GoodNotes' storage mean for Gamma's own
file is in [ink-storage.md](ink-storage.md).

### Against Gamma's ink

| Notability | Gamma today | Gap |
|---|---|---|
| ballpoint / fountain, pressure toggle | Pen or Monoline per pen preset (`markup/MarkupToolbar.jsx`); pressure is also a browser setting, mouse/finger strokes even | dashed / dotted styles |
| 12 sizes, 3 slots shown | 8 widths per kind, each preset holds one | none in effect |
| 32 colours + 8 fast custom | 14 pen / 8 highlighter swatches + a custom colour per preset | none in effect |
| highlighter behind ink | draw order with `multiply` | pen colour over a highlight |
| eraser: partial / whole, 12 sizes, auto-deselect | partial / whole, 3 sizes | return to the last tool |
| draw-and-hold: straight line, shapes | none | hold detection on a still pointer |
| lasso: freeform / boxed; move / scale / rotate / restyle | freeform / box; move, resize, rotate, Color, Width, delete | none in effect (since built: `ink/InkLayer.jsx`) |
| duplicated tool presets, tap the armed tool for its popover | the same: a row of presets, tap again for the options row | reorder by drag, sync across devices |
| stroke: constant width + per-point fractional widths | size + per-sample pressure | same information |

The strip took Notability's shape after a first version (2026-09-13) that
had four fixed tools with a shared colour and S/M/L row: picking a pen and
then a colour is two taps for every switch, and the colour the user set
on the pen was lost when they went to the highlighter and back. A preset
row is one tap per switch and each pen keeps its look.

## Sources

- InkML: https://www.w3.org/TR/InkML/
- Xournal++ stroke model and saver: https://github.com/xournalpp/xournalpp/blob/master/src/core/model/Stroke.h and https://github.com/xournalpp/xournalpp/blob/master/src/core/control/xojfile/SaveHandler.cpp
- tldraw draw shape schema: https://github.com/tldraw/tldraw/blob/main/packages/tlschema/src/shapes/TLDrawShape.ts
- Excalidraw element types: https://github.com/excalidraw/excalidraw/blob/master/packages/element/src/types.ts
- perfect-freehand: https://github.com/steveruizok/perfect-freehand
- pdf.js ink editor: https://github.com/mozilla/pdf.js/blob/master/src/display/editor/ink.js
- reMarkable v6 parser: https://github.com/ricklupton/rmscene
- PencilKit `PKDrawing`: https://developer.apple.com/documentation/pencilkit/pkdrawing-swift.struct ; decode failures across OS versions: https://developer.apple.com/forums/thread/734632
- Pointer Events spec: https://w3c.github.io/pointerevents/ ; Apple Pencil in Safari (pressure, no coalesced events, 240 Hz moves): https://dev.to/sendotltd/reading-apple-pencil-pressure-in-the-browser-pointerevent-getcoalescedevents-and-the-2e23 and https://developer.apple.com/forums/thread/689375
- Upstream fork design doc: https://github.com/amogadget/Gamma/blob/v0.4.0/docs/design/handwriting-recording-ipad.md
- Notability support: Writing with Apple Pencil https://support.gingerlabs.com/hc/en-us/articles/218333197 ; Highlighter https://support.gingerlabs.com/hc/en-us/articles/4968218861978 ; Eraser https://support.gingerlabs.com/hc/en-us/articles/360029432891 ; Select Tool https://support.gingerlabs.com/hc/en-us/articles/360018646412 ; Perfect Shapes https://support.gingerlabs.com/hc/en-us/articles/226905028 ; Custom Colors https://support.gingerlabs.com/hc/en-us/articles/360019098351 ; Customize your Toolbox https://support.gingerlabs.com/hc/en-us/articles/6272405402650 ; Settings https://support.gingerlabs.com/hc/en-us/articles/5955260981786 ; Keyboard shortcuts https://support.gingerlabs.com/hc/en-us/articles/360021489291 ; Calligraphy Pen https://support.gingerlabs.com/hc/en-us/articles/11113646664218 ; Pencil https://support.gingerlabs.com/hc/en-us/articles/5363620836634 ; Squeeze gestures https://support.gingerlabs.com/hc/en-us/articles/7316896037786
- Notability engineering blog, object selection: https://blog.notability.com/post/notability-object-selection-adventures-in-vector-graphics
- Notability `.note` format (community): https://jvns.ca/blog/2018/03/31/reverse-engineering-notability-format/ , https://alicja.dev/blog/2019/02/27/retrieving-drawings-from-Notability.html , https://github.com/mrandri19/notability2svg-python
- Notability tool counts and highlighter order (community): https://tech.mountdesales.net/?p=1540 , https://beingpaperless.com/notability-2/
