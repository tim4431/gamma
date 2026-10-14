# PDF scroll flash, and what highlights cost a scroll

The question: on a 4K screen at 200% the PDF viewer flashed on a single wheel
notch, as if the page wobbled for a frame. It went away when the window, or
the pane, was narrowed. What was it, and what do a highlight-heavy and a
long document add? Measured in October 2026; the mechanics now in force are
in [dev/pdf_loading.md](../dev/pdf_loading.md#high-zoom-and-touch-scrolling).

## How it was measured

An isolated server (the e2e harness) with a real paper (a 16-page Nature
article, 4.5 MB), driven by headed Chromium on the developer's machine
(RTX 4070 SUPER, 3840 × 2160 at 200%, so DPR 2), window 1920 × 1080. The
viewer pane is then 1379 × 1030 CSS px, and fit-width draws the page
1369 px wide, about 230%. Windows was set to 6 wheel lines per notch, which
Chromium turns into one discrete 200 px jump, with no animation.

Per scroll: `scrollTop` and every write to a page canvas, per frame; the
frames the browser presented (CDP screencast), each matched to the previous
one by the vertical shift that fits best, so a part of the view that changes
in place shows as a residual; long animation frames; CPU profiles and
Chromium traces. Playwright's plain `mouse.wheel` reproduces the discrete
notch. `Input.synthesizeScrollGesture` reproduces a smooth one, and only
showed the flash once a notch passed 111 px.

## Finding 1: the flash is detail mode's base

At that zoom the canvas cap leaves a whole-page canvas 1.84 backing pixels
per CSS pixel where the screen wants 2, so the page is in detail mode
(`basePlan`). Detail mode then halved the base to 0.92. The detail canvas
reaches only 111 px past the view (`windowMargin`: the viewport alone uses
most of the 8 Mi budget at DPR 2), so every 200 px notch left about 90 px
at the bottom showing the half-resolution base. The redraw landed 13 to 30 ms
later and the strip snapped sharp, a one- or two-frame flash on every notch.
In the presented frames it was a band at the bottom of the view changing in
place, with residuals of 0.96 to 1.24 where a clean scroll leaves 0.01.

A narrower pane lowers the fit-width zoom under the cap (about 205% for A4
at DPR 2), the page is one canvas, and nothing is swapped. Notches of 100 px
stay inside the margin, which is why the design's own checks missed it.

First built: the base keeps its capped resolution in detail mode. Same
scroll, the residual dropped to 0.27 to 0.57 and the strip is close to
identical to the sharp redraw. Frame times did not change (old and new
builds both 18 to 33 ms per frame with capture off). Memory per page in the
look-ahead is what it was just below the cap; the research note
[pdf-zoom-rendering.md](pdf-zoom-rendering.md) had halved it to save that.

That was not enough. Every zoom past about 205% still flashed, worst with
the Notes and Chat panels closed: the pane is then 1920 × 1030, fit-width is
about 320%, the capped base has 1.31 pixels per CSS pixel, and the detail
canvas reaches 16 px past the view, so a notch showed 184 px of soft base
(residuals up to 1.4). Under an 8 Mi budget the viewport alone at DPR 2
leaves no room, whatever the raster does.

Then built: the canvas limits depend on the engine (`canvasLimits`). WebKit
and touch-first devices keep 8 Mi and 4096 per edge; desktop Chromium and
Firefox get 16 Mi, the largest canvas WebKit itself accepts, and 8192 per
edge. Discrete 200 px notches at DPR 2 in gamma-light, presented frames
compared:

| Pane, zoom | Canvases | In-place change after a notch |
|---|---|---|
| 1379 px, 230% (fit-width) | one whole-page canvas at 2 px per CSS px, nothing redrawn while scrolling | none |
| 1920 px, 320% (fit-width) | base at 1.86, detail reaching about 220 px past the view, redrawn 20 ms after each notch | none (residual 0.000) |
| 1379 px, 400% | base at 1.49, detail reaching about 370 px past the view | one 13-row band at 0.064 |

Memory under the desktop limits: up to 64 MB for each page's base in the
look-ahead and 64 MB for the detail, about twice the iPad figure. The
research note's renders were 1.1 to 3.3 times slower for a canvas four times
the size; these run ahead of the view, inside the look-ahead and the margin.

## Finding 2: highlights cost every pass the browser makes

Rectangles drawn per highlight line, three lines to a highlight, a note on
every fourth:

| Document | Frames during a notch |
|---|---|
| 16 pages, no highlights | 16 to 19 ms |
| 16 pages, 320 highlights (960 rectangles) | 4 to 9 frames of 37 ms each |
| 160 pages, no highlights | 17 to 33 ms; detail lands in ~20 ms; a hit test takes 0.29 ms |
| 160 pages, 3,200 highlights | 52 to 68 ms; 0.7 s stalls as pages enter the look-ahead; a hit test takes 28.6 ms |

The highlights' `mix-blend-mode` is not the cost: turning it off changed
nothing. Hit testing is. Chromium runs about six per notch (the hover update
after a scroll, and its ad-detection checks at each commit), and a hit test
walks every paint layer. Blink gives every positioned element its own, so
each highlight rectangle on every page was one (9,600 here, plus 800 note
badges). Drawing highlights, find marks and link boxes only inside the
900 px look-ahead (built) leaves the viewer 1,457 positioned elements in
all, text layer spans included, and the hit test at 18.6 ms.

The rest is the Notes panel next to the viewer, which renders every
highlight block: 84,002 elements, 20,000 positioned, 16,800 stacking
contexts for 4,000 blocks. Each block row carries eight paint layers
(`sortableBlockWrap`, `blockRowWrap`, `blockRendered` and `rowHandles`
positioned, `addHandle` absolute, and three hover controls hidden with
`opacity`). With those rows hidden the hit test is back at 0.29 ms. With
`content-visibility: auto` on them it is 1.76 ms, but Chromium's own
visibility tracking then adds 314 ms over the same scroll, one observer per
row.

The 0.7 s stalls are font loads. pdf.js adds a `FontFace` to the document
the first time a page uses a font, and Blink then invalidates style and
layout for font updates across the whole document: 150 to 170 ms of layout
plus 50 ms of text shaping per load, almost all of it in the Notes panel.
The long document here repeats the article ten times (`import_pages`), so
its fonts are new every 16 pages; a real book loads most of its fonts on its
first pages.

## Still open

- **The Notes panel at thousands of blocks.** Rows outside the panel's view
  could be skipped: windowed rendering, or `content-visibility` on top-level
  rows only, which keeps the observer count down. Both touch the outline's
  drag and drop, scroll-to-block and find, so neither was tried beyond the
  CSS experiment above. Fewer layers per row (the hover controls hidden
  without `opacity`) would help at any count.
- **pdf.js fonts in another document.** `getDocument`'s `ownerDocument`
  could load fonts into a hidden frame whose canvases do the rendering, so
  a font load would no longer re-lay-out the app. Not tried.
- **A fast spin still outruns the margin.** Several notches inside one
  redraw show the base until the scroll rests, as designed; on a desktop
  that base is now near the screen's resolution.
- **Engine detection** keys on `GestureEvent` (WebKit only) and a coarse
  primary pointer. An Android tablet driven by a mouse would get the desktop
  limits; not checked on a device.
- **The probe is not in the suite.** Its frame timings depend on the GPU
  and the machine's load. The capped base and the limits are pinned by
  `tests/pageRaster.test.mjs`, `tests/canvasSize.test.mjs` and
  `tests/e2e/scenarios/pdfTouch.mjs` (which runs as a touch device, so on
  the iPad limits).
