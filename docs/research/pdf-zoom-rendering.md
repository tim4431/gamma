# Sharp PDF pages at high zoom: what was measured, and the design recommended

Survey from 2026-10-04. A page zoomed past roughly 200 to 300% looks soft on
a high-DPI screen. This note records why, what pdf.js's own viewer does about
it, what each option costs on Gamma's code as measured, and the design that
follows. The design was built on 2026-10-05; the mechanics are in
[dev/pdf_loading.md](../dev/pdf_loading.md#high-zoom-and-touch-scrolling),
and the differences from this note are listed at its end.

## Why a zoomed page is soft

`PdfPage` (`frontend/src/pdf/PdfViewer.jsx`) draws each page as one canvas
covering the whole page, and `shared/lib/canvasSize.js` holds that canvas to
8 Mi pixels and 4096 pixels per edge so an iPad never refuses the allocation.
The page box keeps growing with the zoom; the bitmap stops growing and is
stretched. For A4 the cap leaves about 4.09 / zoom canvas pixels per CSS
pixel, so the page falls below the screen's own resolution at:

| Device pixel ratio | Soft above |
|---|---|
| 1.0 | never under the 400% limit (the 2x supersampling is lost above 205%) |
| 1.25 | 327% |
| 1.5 | 273% |
| 2.0 | 205% |
| 3.0 | 136% |

Fit-width in a wide pane is already about 235% for A4, so a DPR 2 screen can
be past the limit without any zooming. Everything else on the page (text
layer, links, highlights, ink) is DOM or SVG and stays exact at any zoom.
Only the canvas is soft.

## What pdf.js's own viewer does

Gamma calls pdf.js's core API directly and does not use its `PDFViewer`
component, so none of this arrives with an upgrade. It is the reference
design, read from `pdfjs-dist` 6.4.299 (`web/pdf_viewer.mjs`):

- **A detail canvas** (5.0, [PR 19128](https://github.com/mozilla/pdf.js/pull/19128)).
  When the whole-page canvas would pass the pixel limit, the viewer keeps a
  whole-page canvas at reduced resolution and draws a second canvas on top
  that covers only the part of the page near the viewport, at the screen's
  full pixel ratio. The render is an ordinary `page.render` with a
  translating `transform`; pdf.js still walks the page's whole drawing list
  and the canvas clips it.
- **Sizing.** The detail area is the visible slice padded by up to 100% on
  each side, less when the padded area would pass the pixel limit. The base
  canvas drops to half its capped linear resolution while a detail canvas is
  in use.
- **Scrolling.** The detail canvas is redrawn when the visible slice leaves
  it or the padding becomes lopsided. A redraw that was cancelled by further
  scrolling is not restarted until scrolling stops, so a fast scroll shows
  the base and sharpens on rest.
- **Zooming.** The old canvases are stretched by CSS and the redraw is
  postponed by a caller-supplied delay.
- **Skipping operations** (5.4, [PR 19043](https://github.com/mozilla/pdf.js/pull/19043),
  off by default in the viewer). `page.render({ recordOperations: true })`
  records a bounding box per drawing operation into `page.recordedBBoxes`,
  and a later render can pass `operationsFilter` to run only the operations
  that touch the detail area. Both are in the core API, so a custom viewer
  can use them. Recording needs the `canvas` render parameter; it is skipped
  for a bare `canvasContext`.

Gamma already uses the translating-render trick in two places: the highlight
capture and the area-note crop in `PdfViewer.jsx`.

## Measurements

A standalone page in Playwright's Chromium (headless, GPU on, RTX 4070
SUPER) rendered sample pages into offscreen canvases the way each strategy
would, with a one-pixel readback to force the raster. Median of three after a
warm-up, so the drawing list, fonts and images are already loaded. Samples:
the first page and the page with the longest drawing list of arXiv
1706.03762, 1512.03385 and 2103.00020, a synthetic figure of 121,000
operations, and a synthetic 300 dpi scan. pdf.js yields to the next animation
frame between 15 ms slices, so about 17 ms is the floor and reads as "one
frame". The script was a one-off and is not in the repository.

Letter page at 400% on a DPR 2 screen (page box 2448 x 3168 CSS px, pane
1400 x 900), pdf.js 4.4.168, milliseconds:

| Page (drawing operations) | Today: whole page, capped (8.4 MP, 1.04 px per CSS px) | Whole page at DPR 2, no cap (31 MP) | Detail canvas (8.4 MP, 2 px per CSS px) | Reduced base (2.1 MP) |
|---|---|---|---|---|
| 1706.03762 p. 1 (247) | 15 | 20 | 16 | 10 |
| 1512.03385 p. 5 (3,052) | 18 | 60 | 21 | 18 |
| 1706.03762 p. 14 (13,538) | 86 | 223 | 145 | 50 |
| 2103.00020 p. 41 (31,188) | 94 | 196 | 74 | 54 |
| Synthetic figure (121,366) | 458 | 923 | 530 | 248 |
| 300 dpi scan (5) | 16 | 17 | 10 | 20 |

The first pages of the other two papers behaved like the first row. A second
run split the detail area into tiles, each its own render:

| Page | One detail canvas | 2x2 tiles | 4x4 tiles |
|---|---|---|---|
| 1706.03762 p. 1 | 16 | 66 | 265 |
| 1512.03385 p. 5 | 24 | 53 | 297 |
| 1706.03762 p. 14 | 149 | 271 | 788 |
| 2103.00020 p. 41 | 90 | 200 | 676 |
| Synthetic figure | 584 | 659 | 1178 |
| 300 dpi scan | 15 | 65 | 261 |

What the numbers say:

- **A detail canvas costs about what today's render costs**, because it has
  the same number of pixels, and it is sharp where today's is not. Ordinary
  text pages take one frame.
- **Its cost does not grow with zoom.** At 800% the same pages took 8, 16,
  97, 57, 479 and 16 ms, while today's whole-page canvas would be down to
  0.52 pixels per CSS pixel.
- **Raising the cap is the worst trade.** It is 1.1 to 3.3 times slower,
  needs 124 MB for one page at 400%, cannot reach 800% at all, and is exactly
  the allocation the cap exists to keep off iPads.
- **Tiles lose.** Every tile replays the page's whole drawing list and pays
  at least one frame, so four tiles cost 1.1 to 4.3 times the single canvas
  and sixteen cost 2 to 17 times. Tiles only pay when each one can be
  cached for a long time, which a scrolling viewport at a changing zoom does
  not give.
- **The text layer is rebuilt on every zoom step** and that costs as much as
  the raster: `getTextContent` took 5 to 21 ms and building the spans 9 to
  34 ms, 15 to 38 ms together per page (69 to 491 spans). The rebuild is not needed. pdf_viewer.css
  sets `--scale-factor: 1` on `.pdfViewer`, so the layer is laid out at scale
  1 and Gamma already sizes it with `transform: scale()`.

pdf.js 6.4.299 with the operation filter, same pages:

| Page (operations as 6.4 counts them) | 400%: detail, then filtered (operations kept) | 800%: detail, then filtered (operations kept) |
|---|---|---|
| 1706.03762 p. 14 (12,385) | 352, 282 (77%) | 310, 205 (26%) |
| 2103.00020 p. 41 (26,881) | 54, 39 (47%) | 48, 24 (14%) |
| Synthetic figure (60,966) | 550, 580 (53%) | 489, 433 (17%) |
| The three first pages | one frame either way | one frame either way |

The filter helps the heavy pages by up to half at 800% and by a quarter or
less at 400%. On one sample (1706.03762 p. 14) 6.4 took 220 ms for the
whole-page render that 4.4 does in 86 ms; that was not investigated. The upgrade is not
needed for this work and is not an obvious win, so it stays a separate
question.

A visual check of one region at 400% and DPR 2 confirmed the point of the
exercise: the capped canvas is visibly soft, the detail canvas is crisp, and
the two line up.

## Options weighed

| Option | Verdict |
|---|---|
| Raise or remove the canvas cap | Declined. Slower, four times the memory at 400%, a hard ceiling just above it, and it breaks iPads. |
| Fixed tiles with a tile cache | Declined. Measured 1.1 to 17 times the cost of one canvas, plus cache bookkeeping. |
| One detail canvas over a reduced base | Recommended. Same cost as today, sharp at any zoom, memory bounded by the screen. |
| Upgrade pdf.js for `operationsFilter` | Deferred. Small gain at the zooms people use, an unexplained regression on one page, and its own migration (`canvas` parameter, wasm decoders, text layer changes). The design below takes the filter later without changing shape. |
| Adopt pdf.js's `PDFViewer` | Declined. It owns the scroller, page DOM, zoom and page lifecycle, which Gamma's skeleton layout, overlays, zoom anchoring and document cache are built on owning. |
| Render on the server, or a second engine (pdfium in wasm) in a worker | Not measured, declined on shape. The server's pdfium is behind one lock, and a second parser per document doubles memory and can disagree with the text layer's geometry. |

## The design recommended

Organizing idea: **the page box carries the zoom, the bitmap only ever covers
what the screen can show.** A whole-page canvas is the preview; a detail
canvas is what the reader looks at.

1. **When.** Detail mode is on for a page when `canvasSize` would leave the
   whole-page canvas below the device pixel ratio. Below that zoom nothing
   changes: one canvas, supersampled as today. At DPR 1 that means never
   under the 400% limit.
2. **The base** in detail mode is the whole page at half today's capped
   linear resolution (a quarter of the pixels). It is what a fast scroll and
   the first frame after a jump show.
3. **One render window for the viewer.** The viewport is inflated by a margin
   on each side, `m = clamp((sqrt(budget / (w * h * dpr^2)) - 1) / 2, 0, 1)`
   of the viewport's own size, with the same 8 Mi budget. Each page's detail
   canvas is the page's intersection with that window, drawn at the device
   pixel ratio through `canvasSize`. The pages' slices are disjoint parts of
   one window, so the total stays inside the budget by construction. pdf.js
   budgets per page; one window is simpler and has a hard total.
4. **Scrolling.** Only pages that intersect the viewport listen to the
   scroller (two or three at most), coalesced to a frame. A page redraws its
   detail canvas when the visible slice is no longer covered: at once if
   nothing is in flight, otherwise after about 120 ms without scrolling, and
   a render whose target stopped covering the view is cancelled. Slow
   scrolling stays inside the margin and stays sharp; a fast scroll shows the
   base and sharpens on rest.
5. **Zooming.** Both canvases are sized in percent of the page box, so they
   stretch with it in the same commit, as the single canvas does today. The
   raster restarts about 150 ms after the last zoom commit instead of on
   every wheel frame; today each frame starts a render per visible page and
   cancels the previous one.
6. **Order.** A page that intersects the viewport draws its detail canvas
   first, then its base. A page that is only inside the 900 px look-ahead
   draws just the base.
7. **The text layer survives a zoom.** Loading the page, building the text
   layer and reading link annotations move out of the effect that depends on
   the scale; a zoom only updates the layer's transform. This removes the measured 15
   to 38 ms per visible page per zoom step and keeps a text selection alive
   across a zoom. `PdfCitationOverlay` measures the layer's spans and has to
   re-measure when the scale changes instead of waiting for a rebuild.

Shape in the code:

- `pdf/pageRaster.js`: the pure decisions (base size and whether detail mode
  is on, the render window, a page's detail rectangle, whether a drawn
  rectangle still covers the view), unit-tested like `pdfSource.js`, and one
  `renderRegion(page, …)` that every partial render goes through. The detail
  canvas, the highlight capture and the area-note crop become three callers
  of it. The area crop reads the on-screen canvas today and would lose
  resolution under a reduced base, so it has to move anyway.
- A raster hook used by `PdfPage` owns the canvases. `.pdfPageCanvas` becomes
  a wrapper element holding both, because the theme treatment lives on that
  class: sepia themes multiply the canvas at 0.82 opacity and the dark page
  inverts it. Two stacked canvases each carrying the blend would darken the
  overlap; one wrapper carrying it composites them first. pdf.js paints an
  opaque white background, so the detail canvas fully hides the base under
  it.
- `canvasSize.js` stays the only place the limits live.
- With pdf.js 5.4 or later, `renderRegion` can pass `operationsFilter` from
  the base render's recorded boxes. Nothing else changes.

Canvas memory at 400%, DPR 2, Letter: today 8.4 MP per rendered page, one or
two pages, 34 to 67 MB. Recommended: 2.1 MP per rendered page plus at most
8.4 MP of detail in total, 42 to 50 MB.

Checks the change needs: unit tests for the rules in `pageRaster.js`; in
`tests/e2e/scenarios/pdfTouch.mjs`, at 400% and DPR 2, a detail canvas with
at least two backing pixels per CSS pixel over the viewport, total page
canvas pixels inside the budget, the detail canvas following a scroll, both
canvases released for distant pages, and the existing paper-colour check in a
sepia theme (it would catch a doubled blend). That scenario selects the page
bitmap as `[data-page] > canvas` and needs the new wrapper.

## As built

Built as recommended, with these differences:

- The margin of the render window is at most half the viewport's size on each
  side, not all of it, and it respects the 4096 pixel edge limit as well as
  the area budget. A tall phone viewport otherwise got a detail canvas softer
  than the screen.
- A detail canvas is redrawn a little before the view reaches its edge (when
  a quarter of the margin is left), not when the view has already left it.
- A slice drawn for an earlier view is kept until it stops covering the view
  or its page leaves the window, so the total is about one budget and not a
  hard one.
- The text layer is not only left alone on a zoom: once the zoom settles its
  spans are re-measured with pdf.js's `TextLayer.update`, which keeps the
  alignment a rebuild gave. `PdfCitationOverlay` needed no change; its marks
  are percentages of the page box.
- The raster is a controller in the style of `installViewerZoom`
  (`installPageRaster`), not a React hook.
- `cropPage` passes its canvas through the canvas limits, which the highlight
  capture did not do before.

## Not measured, and what stays open

- **iPad and WebKit.** Every number is desktop Chromium on a fast GPU. The
  design allocates no more canvas than today, but render times and
  allocation behaviour on a real iPad need a device.
- **The whole app.** The benchmark rendered outside React. What a zoom commit
  costs across a long document, where every `PdfPage` re-renders because
  `scale` is a prop, was not measured and is not addressed here.
- **A render queue.** Visible pages render concurrently, as today. If
  profiling shows dropped frames with a heavy figure in view, the renders
  can be serialized with the viewport's page first.
- **The zoom limit** was raised from 400% to 800% once the detail canvas was
  in: at 800% the canvases measured the same size as at 400% (a 2.1 MP base
  and a 4.7 MP detail canvas at 1024 x 768, DPR 2). Two things were not
  checked. Browsers cap how tall a scroller's content can be, and a document
  of a few thousand pages at 800% may reach that. The live-ink canvas still
  covers the whole page, so a stroke being drawn at 800% is soft until the
  pen lifts and it becomes SVG.
- **pdf.js 6.x.** Worth its own look: the operation filter, and the page
  that rendered slower.
