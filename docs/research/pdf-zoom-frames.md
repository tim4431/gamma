# A zoom frame across a whole document: the scale as a CSS variable

November 2026. The question: what does one Ctrl+wheel frame cost across a
long document, now that each page's raster keeps its own settle
([pdf-zoom-rendering.md](pdf-zoom-rendering.md))? And does it cost less
once the scale reaches the pages as a CSS variable and not as a React prop?
The mechanics now in force are in
[dev/ui-design.md](../dev/ui-design.md#zoom-gestures-in-a-viewer) and
[dev/pdf_loading.md](../dev/pdf_loading.md#high-zoom-and-touch-scrolling).

## How it was measured

The probe was a one-off script against the e2e harness, and is not in the
suite. It used headless Chromium 153, a 1400 × 900 window at DPR 1 and a
fast desktop machine. It ran a train of 20 Ctrl+wheel notches, 40 ms apart,
from fit-width to the zoom limit (994 to 3300 px wide). It recorded
Chromium's script, style and layout time over the train (CDP
`Performance.getMetrics`), as the median of three runs. A stand-in React
DevTools hook, installed before the app loads, counted each commit and
whether App, the viewer and the pages rendered in it.

The documents were made by the harness's `makePdf`. Three had 24 lines a
page, at 1, 16 and 300 pages. One had 16 pages with six highlights a page,
two of them with notes. One had 16 pages of six paragraphs each, every page
translated ("Translate whole document", with a stubbed engine).

## Before

Every PdfPage took `scale` as a prop, so React.memo failed for every page
on every commit. Each one rewrote its box, its text layer's transform and
the px places of its overlays. The translated view was the worst case: its
layer was drawn on every translated page, not only near the view, and
TransPara's fit loop forced a layout per pass for every paragraph on every
frame.

| Document | PdfPage renders | Script | Style | Layout |
| --- | ---: | ---: | ---: | ---: |
| 1 page | 21 | 212 ms | 23 ms | 14 ms |
| 16 pages | 345 | 167 ms | 27 ms | 12 ms |
| 300 pages | 6,308 | 180 ms | 98 ms | 23 ms |
| 16 pages, highlights | 345 | 117 ms | 15 ms | 5 ms |
| 16 pages, translated | 345 | 127 ms | 92 ms | 124 ms |

The train took 21 frames. App rendered twice in each frame: once for the
zoom, and once for the viewer's echo of the effective scale.

## The CSS variable, and the trap in it

The viewer writes the committed scale as `--viewer-scale` on its content
layer. A page box is `calc(var(--viewer-scale) * Wpx)`, and its overlays
are percentages of the box. A plain custom property inherits, so a change
to it restyles every element under the layer, every text-layer span and ink
path. A DOM probe measured the style cost of one frame at 300 pages in the
app's DOM, with React not involved:

| How the scale reaches the pages | Style per frame |
| --- | --- |
| Inline writes on each page (before) | 4.1 to 4.9 ms |
| An inherited variable | 7.9 ms |
| A registered, non-inheriting variable, picked up by the page box and its text layer | 4.5 ms |
| A registered variable read by the page boxes only | 2.1 ms |

So the variable is registered as a `<number>` that does not inherit, and
the empty text layers of pages never built do not pick it up. Only what
sizes with the zoom picks it up with `--viewer-scale: inherit`: a page box,
its translation layer, a built text layer, a notebook's sheet.

WebKit 26.6 (Playwright's build, run here from libraries unpacked into a
scratch folder, since `install-deps` needs sudo) had one more trap. A child
that picks up the registered variable with `inherit` follows its parent's
changes only when the child's inline style was written by script. A child
with no inline style, or one only parsed from markup, keeps the value it
was first styled with. Chromium 153 and Firefox 155 follow in every case.

| Child's inline style | Chromium 153 | Firefox 155 | WebKit 26.6 |
| --- | --- | --- | --- |
| none | follows | follows | stays at the first value |
| parsed from a `style` attribute | follows | follows | stays |
| written through the CSSOM | follows | follows | follows |
| parsed, then written through the CSSOM | follows | follows | follows |

The probe changed the parent's value from 2 to 3 and read the child's
width, which used `calc(var(--v) * 1px)`. This looks like WebKit's
matched-properties cache handing the element its earlier style. React
writes `style` through the CSSOM, and so does the text layer's build, so
every element that picks the variable up is in the safe row. The
registration is made from script (`registerScaleVar` in
`shared/lib/viewerZoom.js`), not by an `@property` rule, so that it can
check that case first. It registers a throwaway property, writes a parent
and a child the way the app does, changes the parent and reads the child.
A browser that fails, or has no `CSS.registerProperty` (Safari before
16.4), keeps the inheriting variable. That is slower per frame, and the
picture is the same.

## After

The pages take no scale prop. Code inside a page that needs a number reads
the viewer's `pdf/pageScale.js`. The raster gets the live value and keeps
its own settle. The text layer is re-measured at the settled value, once per
zoom. A translation was first laid out again at that value too. On merging
with dev's translation work, whose layer is laid out once at a reference
scale (`transBlock.js` REF_SCALE) and carried by one transform, that
transform reads `--viewer-scale` instead, so a zoom renders and lays out
no translation at all.

| Document | PdfPage renders | Script | Style | Layout |
| --- | ---: | ---: | ---: | ---: |
| 1 page | 1 | 58 ms | 8 ms | 4 ms |
| 16 pages | 25 | 62 ms | 11 ms | 5 ms |
| 300 pages | 309 | 96 ms | 38 ms | 22 ms |
| 16 pages, highlights | 25 | 82 ms | 11 ms | 5 ms |
| 16 pages, translated | 25 | 61 ms | 16 ms | 5 ms |

The counter counts each page once in the first commit it sees, before and
after alike. Less that, a train renders 9 pages now: those crossing an
observer margin as the zoom grows. Before, it was about 20 renders of every
page. Script time falls by half to two thirds. The translated document's
layout falls from 124 ms to 5 ms over the train: the paragraphs stretch
with their layer and fit again once, at rest. The 1-page figure before is
high for one page, and was the probe's first document, so part of it is a
cold start.

## Checks

- The pdfTouch step "a Ctrl+wheel train resizes the pages without
  rendering them" counts page 1's renders with the same kind of hook. It
  asserts none during a 12-notch train and at least one at rest, and that
  no style is written on the page's box, text layer, highlight or badge.
  Its positive controls (React found the hook, the viewer rendered in most
  frames) make a counter that stops working fail the step. Against the
  build before the change it fails with 12 renders during the train.
- The translation step zooms a shown translation with the button and then
  the wheel. The layer stays the page box in every frame, the scroll area
  does not widen, no paragraph is laid out again until rest, and the masks
  and formula crops are right after it.
- Both pass in Chromium, Firefox and WebKit 26.6. WebKit's page process
  crashed now and then in the translation group on this host, at the same
  places and earlier with the build before the change. That points at the
  unpacked libraries, not the change.

## What stays open

- **App's two renders a frame.** The second is the viewer's echo of the
  effective scale (`onEffectiveScale`). It is now the largest share of a
  frame's script on a short document.
- **Comment-margin mode.** A document with a comment thread still renders
  every page per frame. The card column's offset is worked out in px from
  the scale. A `max()` of the viewer's width less `var * W` could take it
  into CSS.
- **The translation on distant pages.** It is drawn inside the look-ahead
  only, as the other overlays are, and needs no layout for a zoom.
- **iPad.** Every number is desktop Chromium. WebKit was checked for
  correctness, not timed, and WKWebView on a real iPad is not measured.
