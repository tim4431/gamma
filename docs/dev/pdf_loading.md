# PDF loading

How a PDF gets from the workspace's `uploads/` to a painted page in the
viewer, and why each part is shaped the way it is. The measurements at the end
are what the design is judged by; rerun them before changing any of it. The
survey that led here is [research/pdf-loading.md](../research/pdf-loading.md).

The idea in one sentence: **the server already knows the document, so the
client lays it out before it parses, picks its transport by size, and never
throws a parsed document away.**

## The manifest (`gamma/pdf_meta.py`, `GET /api/pdf-info/{doc_id}`)

Every stored PDF gets one row in the workspace's `data.db`, table `pdf_docs`:
byte size, page count, and every page's size in PDF points with `/Rotate`
applied (the same box pdf.js measures its scale-1 viewport from, so a layout
built from it is exact). It is derived data next to the document text index, so
its `CREATE TABLE IF NOT EXISTS` is in `db.DATA_SCHEMA`, which every
`data.db` connection applies (no migration step); it lives in backups
harmlessly, and is purged with the index when no page carries the document
any more (`search.index.purge_unused`; a replaced file's, `purge_doc`).

Who writes it: `storage.store_pdf` (imports, a PDF the fetch handoff held),
`storage.store_pdf_stream` (uploads, a file chip's PDF among them) and
`storage.store_pdf_path` (an upload sent in parts, `upload_parts.finish`;
[api.md](api.md) "/uploads/parts"), the `/api/pdf` proxy's save path and
`/api/clip` schedule it on a background thread the moment the file lands, so
the first open finds it ready; the search indexer computes it while it has
the file open anyway; and the endpoint computes it on demand for anything
older, in PyMuPDF, in FastAPI's threadpool (the route is a sync `def`). A walk
already running for the document is joined, not repeated, so opening a book
right after uploading it waits for the upload's own walk instead of queueing
a second one behind the PDF reader's lock.

Every manifest walk is `pdf_text.page_sizes`, behind the same lock as text
extraction ([ai_context.md](ai_context.md) "Extraction lock" has the lock's
story). Each size is `page.rect`, the page box with `/Rotate` applied,
divided by the page's `/UserUnit`: MuPDF folds that key into `page.rect`,
pdf.js's scale-1 viewport (4.4.168) does not, and the manifest must be in the
viewer's points (`pdf_text.user_unit`). MuPDF has the box once the page is
loaded, without running its content stream, so the walk is cheap: 67 ms
over a 484-page book and 44 ms over a 1,310-page one
([research/pypdfium2-vs-pymupdf.md](../research/pypdfium2-vs-pymupdf.md)).
The lock is taken per page (and once each for the open and the close), not
for the whole document, so a manifest walk runs between the pages of a book
the search indexer is reading. Every page it loads is dropped, and the
document closed, explicitly inside the lock: PyMuPDF frees its objects by
reference count, so that is the end of them, and nothing is finalized later
on another thread. A file PyMuPDF cannot read is stored with `pages: 0` so
it is not parsed again on every open. The endpoint sends that answer
`no-store` and a real one with a day of `private` caching (a doc id is a
content hash, its manifest never changes). When PyMuPDF itself does not
load, nothing is stored and the endpoint fails, so the viewer takes the
path without a manifest and the file is measured once the reader loads.

Access is the file's own rule: a workspace member, or a share token confined
to the shared page's document (`_share_can_read_upload`). The check reads the
shared pages only and remembers a yes for a few minutes, so a PDF opened by
range requests asks once, not per chunk.

## The client (`src/pdf/PdfViewer.jsx`, `src/pdf/pdfSource.js`)

`pdf/pdfSource.js` holds the pure decisions, unit-tested in
`tests/pdfSource.test.mjs`: which URL is an upload (`docIdOf`), which
transport to use (`chooseTransport`), the pdf.js options of a range open
(`rangeOpenOptions`), how a manifest becomes a page layout
(`layoutFromManifest`, refusing anything that does not describe a readable
document), and which page stands for the document's size
(`typicalPageIndex`: the widest page at most 5 % wider than the most common
width, to the point). Fit-width sizes to that page, not to page 1: a scanned book's
cover is often an image of another size, and fitting it pushed every text page
past the viewer's edge. The widest near it, because a page even a pixel wider
than the viewer lets the view slide sideways at fit-width: a scan's pages, or
A4 and Letter pages merged into one file, differ by a few points. The viewer's load effect composes them:

1. **A parsed document from a recent visit** (`DOC_CACHE`, the last two
   `PDFDocumentProxy` objects with their layout) is committed as it is, in
   the commit that asks for it and before paint (a layout effect), at the
   fit-width scale worked out for it there: the viewer's first frame holds
   its pages at their zoom, with the pixels they showed when they left
   (below, "Coming back to a document").
   Documents are never destroyed on a tab switch or a viewer unmount, only on
   eviction, a second after the commit so mounted pages are not torn out from
   under pdf.js. A document a translation job is reading is pinned
   (`translateState.pinned`): eviction passes over it, past the two, until
   the last job reading it ends and lets it go
   ([translation.md](translation.md#outliving-the-viewer)). The
   byte cache (`PDF_CACHE`) holds one entry, which pays for this one:
   re-reading bytes from IndexedDB costs tens of milliseconds, re-parsing
   them is the expensive half. The in-document search (Ctrl+F)
   reads each page's text from pdf.js once per parsed document and caches it
   per parsed document (`SEARCH_TEXT`, a WeakMap freed with the document): the
   first query over a book extracts every page, the next is a regex over
   strings. A query the panel has moved past stops at its next page and
   answers `stale`, which the panel ignores.
2. **Otherwise the manifest and the bytes are fetched in parallel.** On a
   cold open (nothing on screen yet) the manifest alone commits a
   **skeleton**: page boxes of exact size, no document. The host's `"layout"`
   phase applies the pending exact tab position on it, and the last-read-page
   restore (the `read-pos` pref, what a reload or a cold reopen uses) accepts
   the skeleton as "pages in the DOM", so the reader is on their page before
   a byte of the PDF has been parsed, and `PdfPage` places
   highlights and ink into the reserved box, so the overlays are right from
   the first frame. The skeleton keeps its page keys when the document
   arrives: same components, nothing remounts. It is only laid out on a cold
   open; blanking a document already on screen would undo the atomic swap
   that keeps tab switches flicker-free.
3. **Transport by size.** Cached bytes (memory, then IndexedDB) are used as
   they are. For an uncached upload the size comes from the manifest or from a
   HEAD of the file, whichever answers first: a manifest computed for the
   first time (a long book opened the moment it was uploaded) can take
   seconds, and the open must not wait on it. At or under `WHOLE_MAX_BYTES`
   (12 MB) the file is one plain GET, as before: it stays in the browser's
   HTTP cache and lands in IndexedDB. Above that, pdf.js opens the URL itself
   by **range requests**
   (`disableRange: false`, `disableStream: true`, `disableAutoFetch: true`,
   256 KB chunks): the xref, the page tree and only the pages being drawn.
   `disableStream` is the flag that matters; with autofetch off but streaming
   on, pdf.js's full-file reader keeps running underneath and saturates the
   link the ranges race. pdf.js resolves the URL to an absolute one before
   fetching, and the fetch wrapper in `shared/lib/utils.js` tags absolute same-origin
   URLs with the workspace header too — without that the ranges of a document
   in a non-default workspace came back 404, which pdf.js reports as
   "Missing PDF". Browsers never store 206 responses, so three seconds
   after the commit the whole file is fetched once, quietly, into IndexedDB
   (`backfillLocalCopy`), and the second open is warm. The `/api/pdf` proxy
   streams from its upstream and cannot answer ranges: it always downloads
   whole, and it redirects to `/api/uploads` once a local copy exists.
   The IndexedDB cache (`gamma-pdf-cache`, version 2) keeps the newest
   `DISK_CACHE_MAX` papers (30) and at most `DISK_CACHE_MAX_BYTES` (512 MB)
   of them: the buffers sit in one store and their dates and sizes in
   another (`meta`), so the eviction walk reads sizes, never a buffer. A
   buffer that is also handed to pdf.js is copied before it is stored (pdf.js
   transfers it to the worker); the backfill's download is stored as it is.
4. **Layout from the manifest, or measured.** When the manifest describes
   the opened file (same page count) its sizes are used and the eight-page
   measure loop and the 50-page background refinement are skipped, which also
   removes the layout shifting under a scroll restore that the refinement
   caused. Without a manifest (proxy URLs, a failed read) the old path
   measures the first eight pages, estimates the rest from the typical one
   among them (`typicalPageIndex`, so a cover of another size does not set
   the layout or the fit-width zoom) and refines in the background. Bytes from the cache wait at most
   250 ms for the manifest; measuring is still correct, only slower.

**Coming back to a document.** A page with no PDF unmounts the viewer
(a notes page's tab, the library), and a document taking another's place
remounts every page box (they are keyed by the document). Either way
pdf.js draws the pages again, tens of milliseconds for a plain page and
more for one with figures or on a slow device, and until then they would
be blank boxes. Keeping the viewer mounted would keep them, but a hidden
viewer is `display: none`, which takes every page out of its observers'
view and so releases its pixels anyway, loses its scroll, and leaves a
second `.pdfViewer` in the DOM for the app's and the tests' lookups. So
the viewer keeps the pixels instead (`pdf/pageRaster.js`). As a document
leaves, in a layout cleanup that React runs before it takes the boxes
out, or as a swap commits the next one, `keepPixels` copies what each page
in view shows, under the document. The pages in view are the ones the
reader last saw, worked out from the page heights, the scale the zoom
anchor last laid them out at and the last scroll, not from the boxes in
the DOM: a tab brings its zoom in the same commit as its url, so between
two papers read at different zooms the leaving pages are already laid
out at the next one's zoom, under the old scroll, and the DOM would name
other pages. Each copy is at the screen's resolution for the box its
pixels were drawn for, not for the box as it is in that commit. A page
box of that document mounting again starts from its copy (`takePixels`,
then the raster's `prime`, before paint), and its own first paint
replaces it; a primed page keeps the copy through `show(null)` until
then. A primed page counts as in view from that commit (`useWithin`'s
`assume`), so its highlights, note badges, link boxes and margin cards
are drawn in the frame its pixels are, not one after, when its observers
first report; they own it from there. The copies of the last two documents left stay, within 96 MB (32 MB
under the touch and WebKit canvas limits), the oldest going first, and a
document `DOC_CACHE` evicts takes its copies with it (`forgetPixels`).
A document back with a copy on a page in view at the restored place
reports `painted` with `rendered`: the reader has that page, and no
"Rendering page…" pill rises for the switch. Without one the pill stays
until a page paints, as for any document.

The viewer reports the scale its boxes are laid out at with `rendered`
and `layout`, and the host's pending tab position is measured in it. A
remounted viewer starts at a fit-width of 1 while the host still holds
the last viewer's effective scale; measured in that, every return to a
PDF tab would land a fit-width's factor further down. The fit-width
scale is worked out before paint (a layout effect), so a document is
never seen at the scale the one before it fitted. The viewer keeps its
scrollbar's room while nothing scrolls (`scrollbar-gutter: stable`), so
where the scrollbar takes room (Windows, Linux, a Mac with a mouse) the
pages are as wide with it as without it. A document back from the cache
is fitted in an empty viewer, before its pages bring the scrollbar; had
the scrollbar then narrowed the fit, the zoom anchor would have re-placed
it about the viewport's centre, and each return to the tab landed about
5 px higher than the last. A document laid out at
its own scale in the commit that brought it is not re-placed by the zoom
anchor's swap rule, which is for a fit-width settling after the swap; and
the page number shown is synced before paint, after the restore.

**Where a page stands.** The layout model is `pdf/pageLayout.js`, shared
by the page boxes' styles, the viewer's scroll arithmetic and the reads App
and a tab group's view make of a viewer's scroller. Pages are laid out
one under another, `PAGE_GAP` (8 px) apart, or side by side (below,
"Pages that run across"). Each page box is centred in the viewer by its auto
margins, and starts at the viewer's left edge once it is wider than the
viewer. While the document has a highlight with an open comment thread, a
page with room for the comment column stands beside it instead, the two
centred together, and a page without room stays centred
([comments.md](comments.md#the-margin)). The column is inside the page
box, so it scrolls and zooms with the page; the pages' columns stack as
one, each page's cards under the ones above. The zoom anchor's horizontal
arithmetic reads the same three layouts (`pageLeft`,
`comments/marginLayout.js`), before and after the scale changes. A zoom
that makes the column appear or go moves a page that fits the viewer
sideways under the pointer, since there is no horizontal scroll to take
that up; the vertical anchor holds.

**Pages that run across.** Settings → Reading › PDFs › Page direction
(the account pref `pdfFlow`, `down` by default or `across`) lays the
pages side by side, left to right, `PAGE_GAP` apart, and the viewer
scrolls sideways through them, continuously, with no page snapping. App
passes the pref to the viewer as `pageFlow`, and to a tab group's views
through `paneShared`. The viewer marks its scroller with
`data-flow="across"` and the `pdfAcross` class, which makes the zoom layer
one flex row (`app.css`): each page centred in the row's height (the
view's, or the tallest page's once that is taller), and the row centred in
a view wider than all of it. Everything that measures along the pages
measures along the run: the zoom anchor (`pageAt` along the run,
`crossStart` across it in place of `pageLeft`), a jump to a page or a
highlight (`jumpTarget`), the page counter (a third of the way across the
view), `pagesInView` for the kept pixels, and the page in view
(`onPageInView`, nearest by both axes). A jump across stands a page the
view holds whole in its middle; a wider one shows its place `offset` px in
from the left edge, as down shows it `offset` px under the top. The fit
(`page-width`, the strip's button, *Fit page* there) fits the whole
typical page into the viewer, its height too. So that fit holds once the
pages are in, the scroller keeps the horizontal scrollbar's room
(`overflow-x: scroll`), as it keeps the vertical one's down. The comment
column runs down beside the pages, so pages that run across have none:
their threads open in the popover. Turning the pages the other way keeps
the page being read: the viewer lands on it before paint, and at a fit
again once the new fit's scale has committed (`flowPageRef`, which the zoom
anchor takes instead of holding the view's middle). App stamps the
direction on each tab's exact place (`flow` in `tabScrollRef`'s entries
and Back's), and a place measured in the other direction is dropped, so a
tab opens at its last-read page instead. Its read-position tracker and
the last-read-page restore find and check pages along the run
(`pageInMiddle`, `pageLanded`). A mouse wheel turned down over pages that
fit the view's height moves them across (`installWheelAcross`, below);
with something to scroll down, a zoom taller than the view, it scrolls
down as ever. The notebook's sheets always run down.

**The worker.** pdf.js does its parsing in a Web Worker whose script is 1.3 MB.
It is imported as a Vite asset (`pdf.worker.min.mjs?url`), so it is always the
installed `pdfjs-dist` legacy build and is served content-hashed and immutable
like the bundle; one `PDFWorker` is created with the engine and shared by every
document (pdf.js would otherwise start a fresh worker per `getDocument`, and it
destroys only workers it created itself, so a shared one survives cache
evictions). The engine, pdf.js itself (0.38 MB, 0.1 MB as Brotli), is a chunk
of its own: `loadPdfEngine()` fetches it and starts the worker, App calls it
at startup when the address names a page, a share or a PDF and otherwise once
the library has been up for a moment, and an opening document awaits it just
before `getDocument` (the `parsing` phase), so a cold open fetches it beside
the manifest when nothing loaded it earlier ([frontend-refactor.md](frontend-refactor.md#lazy-boundaries)).
A copy under `public/` sent `no-cache` would be re-downloaded on
every page load, because Starlette's `FileResponse` sets an ETag but never
compares one (at 20 Mbps, 0.6 to 0.85 s of every open — the research note has
the measurement). The static route in `gamma/app.py` compares the ETag
itself, so the unhashed files (`index.html`, favicons) get a real 304.

The build writes a Brotli (quality 11) and a gzip (level 9) copy beside every
emitted js, mjs, css, html and svg file of 1 KB or more, where the copy is
smaller. That is the `precompress` plugin in `frontend/vite.config.js`, about
5 s of the build. The static route sends the `.br` copy when the request's
`Accept-Encoding` allows `br`, else the `.gz` one for `gzip`, else the file
itself. Each goes out with the file's own media type and cache rule. A file
with a copy answers `Vary: Accept-Encoding`, and each encoding has its own
ETag, so the 304 above holds per encoding. The worker goes out as 0.3 MB
instead of 1.3 MB, the main chunk as 0.49 MB instead of 1.9 MB. The route
answers HEAD as well. An `/api` path without a HEAD route of its own gets a
405. A reverse proxy that serves `dist` itself can send the same copies
([debugging.md](debugging.md#serving-the-build)).

The backend pins `.mjs` and `.js` to `text/javascript`, independently of OS
MIME mappings (Windows registry entries can otherwise make the worker plain
text). A stable `?mime=js` on the worker URL bypasses old immutable responses
cached with the wrong type. Eager worker startup observes its promise's
rejection; opening a document reports the same failure through the status
pill. A manifest arriving after a worker or download failure cannot lay out
a skeleton and overwrite that error with "Preparing document". The browser
`pdf-load` group exercises a wrong worker MIME followed by a late manifest,
then restores the response and verifies a reload paints the document.

## High zoom and touch scrolling

`shared/lib/canvasSize.js` bounds every PDF and live-ink backing store
(`canvasLimits`). WebKit documents both a
[canvas area limit](https://bugs.webkit.org/show_bug.cgi?id=171238) and
[total canvas allocation failures](https://bugs.webkit.org/show_bug.cgi?id=195325),
and an iPad may report a Mac user agent, so on WebKit (`GestureEvent` exists:
Safari and every iPad browser) and on a touch-first device (`pointer:
coarse`) a canvas stays at 8 Mi pixels and 4096 per edge. Desktop Chromium
and Firefox get 16 Mi, WebKit's own per-canvas maximum, and 8192 per edge.
They need it: at DPR 2 a 1920 × 1030 viewer alone is 7.9 Mi, and under 8 Mi
the detail canvas could reach only about 16 px past it
([research/pdf-scroll-flash.md](../research/pdf-scroll-flash.md)).
Layout, text, links and SVG annotations are DOM and keep the exact zoom
whatever the canvases do.

The page box carries the zoom; the bitmaps only cover what the screen can
show. `pdf/pageRaster.js` holds the rules as pure functions and the
controller (`installPageRaster`) that draws one page's canvases. The survey
and measurements behind it are in
[research/pdf-zoom-rendering.md](../research/pdf-zoom-rendering.md).

- **One canvas while it can be sharp.** A page is a single whole-page canvas,
  supersampled to at least 2 backing pixels per CSS pixel, for as long as
  the cap leaves it at the screen's pixel ratio or better (`basePlan`).
- **Detail mode past that.** For A4 the 8 Mi cap leaves about 4.09 / zoom
  backing pixels per CSS pixel: on a DPR 2 screen detail mode starts near
  205%, on a DPR 1 screen just past 400%. The desktop limits leave about
  5.78 / zoom: near 290% at DPR 2, 580% at DPR 1. The zoom limit is 800%
  (`shared/model/zoom.js`). The detail canvas costs the same there as at
  400%, since it is sized by the viewport. The whole-page canvas
  (the base) keeps its capped resolution. A second canvas (the detail)
  covers the page's part of one render window, at the device pixel ratio.
  The base is what a strip of the view shows until the detail redraw lands.
  It used to drop to half resolution, which on a 4K screen at 200% made
  every wheel notch past 205% flash a blurry strip. A page in the look-ahead
  holds what it held just below the cap, plus its share of one detail
  budget.
- **The render window** is the viewport grown on each side by a margin: what
  the area and edge limits leave once the viewport itself is covered, at
  most half the viewport's size (`windowMargin`). Each page's detail canvas is
  its intersection with that window (`windowSlice`), moved onto the canvas's
  own pixel grid (`detailPlan`). The slices of one window do not overlap, so
  the detail canvases of the pages in view add up to about one budget. A
  slice drawn for an earlier view is kept until it stops covering the view or
  its page leaves the window, so the total can briefly exceed that.
- **Scrolling.** Only pages in detail mode listen to the scroller, coalesced
  to a frame. A page redraws its detail canvas when less than a quarter of
  the margin is left ahead of the view: at once if no render is in flight.
  A render the view has moved past is cancelled, and after one has been
  outrun the next waits for 120 ms without scrolling, so a fast scroll shows
  the base and sharpens on rest. The margin depends on the pane, not the
  zoom. At DPR 2 it is about 370 px each way for a 1379 × 1030 view and
  235 px for 1920 × 1030 under the desktop limits (111 and 16 px under the
  iPad ones). A Windows wheel notch set to 6 lines jumps 200 px, so on a
  desktop a single notch stays inside the detail canvas.
- **One render at a time, the nearest page first.** Every render of a page
  enters its scroller's queue (`pdf/renderQueue.js`, shared by the pages of
  one viewer). The queue starts one at a time, the page nearest the view
  first, so a frame carries at most one pdf.js chunk. A render of a page away from
  the view is preempted when one that touches the view arrives, and starts
  over once that is done (pdf.js can only cancel). While the scroller moves
  faster than `FAST_VIEWS_PER_S` (two viewport lengths a second, heights
  down or widths across, over the last 120 ms) renders away from the view
  wait: a fast scroll passes the pages it
  would draw, and each chunk drawn is a frame the scroll loses. The page in
  view never waits. `tests/renderQueue.test.mjs` pins the order, the
  preemption, the hold and the cancels.
- **Zooming.** Both canvases are sized in percent of the page box, so they
  stretch with it in the same commit. The redraw starts 150 ms after the
  last zoom step (`ZOOM_SETTLE_MS`), the part in view first, then the base.
  A first paint does not wait.
- **How a zoom reaches the pages.** The viewer writes its committed scale
  as `--viewer-scale` on `.pdfZoomLayer`
  ([ui-design.md](ui-design.md#zoom-gestures-in-a-viewer)). A page box is
  `calc(var(--viewer-scale) * Wpx)` wide and high, and picks the variable
  up with `inherit` (`app.css`), as its translation layer does. Highlights,
  badges, link boxes and find marks are percentages of the box. So a wheel
  train, a pinch's commit or a pane drag at fit-width re-renders the viewer
  and none of its pages. What needs the scale as a number reads the
  viewer's store (`pdf/pageScale.js`). The raster gets the live value from
  `get()` and keeps its own settle. A page reads the settled value,
  `rest()`, through `useSyncExternalStore`, subscribed only inside the
  look-ahead. A live change is no new snapshot, so the page renders once
  per zoom, once it rests, and a page away from the view renders not at
  all.
- **What still renders per zoom frame.** The pages of a document in
  comment-margin mode, whose card column sits at an offset worked out in px
  (`marginLeft`, `pageTop`, `marginScale`). An ink layer that redraws on a
  resize (`useGroupRasters`, the ruler), and a page crossing an observer
  margin. And the viewer itself, with App twice over.
- **Why the boxes are not written from the store.** Writing each box's
  size from `pageScale` costs less style than the variable. But the
  document's "rendered" layout effect, which restores the reader's place,
  and the ink layers' own layout effects read layout before such a write
  would land. React writing the variable in the commit keeps every page box
  at its new size before any layout effect runs.
- **The text layer is built once per page**, after the page's first pixels
  and once the scroller has sent no scroll event for 100 ms
  (`afterScrollRest`), and kept across zooms and when the page scrolls away.
  A fast scroll would build it for pages it passes, and under the smooth
  wheel the frame or two it takes is a visible stall. pdf_viewer.css sets
  `--scale-factor: 1` on `.pdfViewer`, so the layer is laid out at scale 1.
  pdf.js sizes it, and the build sets `transform: scale(var(--viewer-scale))`
  and `--viewer-scale: inherit` on it inline, which also overrides
  pdf_viewer.css's rotation transforms. React never writes the layer's size
  or transform. Until it is built, the layer is pdf.js's `inset: 0` and
  fills the box, and an empty layer restyles nothing on a zoom. After a
  zoom settles (`rest()`) `TextLayer.update` re-measures the spans at the
  new size. A text selection
  and the citation marks survive a zoom. Only a page far from the view
  drops it. A second observer with `FAR_MARGIN` (four viewport lengths each
  way, down and across; pdf.js's own viewer keeps about ten pages) empties the layer, takes
  off what the build set on it, and forgets the paint, and the next paint
  builds it again. A book read
  through would otherwise leave every page's spans in the DOM.
- **One renderer.** Every raster goes through `renderRegion`: the base, the
  detail canvas, the highlight capture (`captureRef`) and the area-note
  snapshot (`cropPage`). The last two are drawn from the document, not copied
  off the screen, where the bitmap may be capped below the screen's resolution. The transform is passed
  to pdf.js as its `transform` parameter. pdf.js paints the paper before
  applying its own transform, so a context translated beforehand leaves the
  crop without paper.
- **Drawn in place, never copied.** The base and the detail each have a
  hidden twin in the page box (`pair`; the hidden one carries `data-spare`).
  A render draws into the twin and the two swap once it lands, so the screen
  never shows a half-painted page and the replaced canvas gives up its
  backing store. The browser rasterizes what pdf.js draws when the canvas is
  first used; a page rendered into a canvas outside the document and copied
  in paid for its whole record at the copy, 20 to 33 ms in one frame
  ([research/pdf-wheel-spin.md](../research/pdf-wheel-spin.md)). In the
  document it is rasterized frame by frame beside pdf.js's own chunks.

`.pdfPageCanvas` is a wrapper element around the two canvases. The theme
treatments (multiply and soft-ink opacity in the paper themes, the invert
filter of the dark page) sit on the wrapper, which composites the canvases
before blending. On the canvases themselves the overlap would be blended
twice.

`PdfPage`'s intersection observer is rooted at the PDF scroller with 900 CSS
pixels of look-ahead (`RENDER_MARGIN`). A page inside it is rendered. On a
desktop (the roomy canvas limits) a page that leaves it keeps the pixels it
has, and draws nothing new, until it is `KEEP_MARGIN` away (three viewport
lengths each way, down and across, a second observer), so a scroll back over read pages
redraws nothing. A zoom meanwhile stretches what it holds, and the page is
redrawn when it comes back inside the look-ahead. The pages holding pixels are kept
within `KEEP_BUDGET_BYTES` (256 MB of canvas over the document): past that
the pages farthest from the view let go first, never one inside the
look-ahead nor one rendered on request (`pinned`, the jump target or the
cited page). A touch-first device or WebKit keeps nothing past the
look-ahead. A page past the keep margin releases both backing
stores (`show(null)`) and calls `page.cleanup()`, which frees pdf.js's
operator list and decoded images for the page. The next render asks the
worker again. Its ink and text-box layers are mounted only
inside the look-ahead unless the page carries marks (`MarkupLayers`'s
`active`: each layer listens on the window and the document, and a book has
hundreds of pages). Its highlights, find marks and link boxes are drawn only
inside the look-ahead, whatever the page carries: each is a positioned
element, Blink gives each its own paint layer, and every hit test, layout and
pre-paint walks them all. 3,200 highlights over 160 pages cost 28.6 ms per
hit test, about six of which run per wheel notch (the hover update after a
scroll, and Chromium's ad checks at each commit). The page keeps its
geometry, text and overlay data; it repaints on return without waiting, and
a forced render (jumps, the cited page) still works. A page inside the look-ahead but outside the render window holds only
its base. Release and unmount cancel the pending pdf.js renders.

`pdf/scrollSnap.js` is the always-on one-finger alignment along the pages'
run (`installScrollSnap`, reinstalled on zoom, document and direction
changes). It judges direction after 8 CSS pixels within a 30° cone along
the run: vertical down, horizontal across. It never writes scroll offsets
while a finger or native momentum is moving. After `scrollend` (250 ms of
quiet after lift on browsers without it) it corrects the drift the other
way once (sideways down, up or down across), instantly under reduced
motion. A diagonal start, a deliberate turn away from the run, a second
contact, a cancelled gesture, keyboard or wheel input, a zoom change or
teardown drops the pending alignment. Writing
offsets mid-gesture fights WebKit's native scroll animation
([WebKit issue](https://bugs.webkit.org/show_bug.cgi?id=255193)).

`shared/lib/smoothWheel.js` is the opt-in smooth wheel (Settings → Reading ›
PDFs › Smooth wheel scrolling, the account pref `smoothWheel`, off by
default; `installSmoothWheel`, installed by the viewer while it is on), and
the wheel over pages that run across (`installWheelAcross`, installed
while they do, after the smooth wheel). With nothing to scroll down, a
wheel turned down or up moves pages that run across sideways, which it
would otherwise not move at all: a notch glides there while the smooth
wheel is on (`installSmoothWheel`'s `across`), and any other delta (a
touchpad's, a notch with the setting off) moves them at once
(`acrossDelta`). Shift, Ctrl / ⌘, a sideways delta and a wheel over an
inner element that scrolls itself are left as they are. A
browser without its own smooth scrolling moves a notched mouse wheel in
jumps, 200 px a notch with Windows set to 6 lines. With the setting on, the
scroller glides toward where the notches point: every frame it closes a
fixed share of what is left (`approach`, 95% of a notch in `SMOOTH_MS`,
150 ms), and a notch on the way only moves the goal, so the motion carries
on. The glide is timed by the frame clock, never the wheel's: Chromium hands
a wheel event to the page inside its frame, before that frame's animation
callbacks, whose timestamp is the frame's start and so precedes the event. A
glide measured from the event found no time passed in its first frame and
stood still for a frame per notch, which a fast spin, a notch to a frame,
turned into a standstill ([research/pdf-wheel-spin.md](../research/pdf-wheel-spin.md)).
Only a notch is taken (`notchDelta`): a line- or page-mode event, or a
pixel-mode one whose legacy `wheelDeltaY` counts whole notches
(`wholeNotches`). That value is 120 a notch, but Chromium divides it by
`devicePixelRatio` and truncates it (`wheel_event.cc`), so a notch reads
109 at 110% page zoom and 96 on a 125% display; the check undoes that
division, and also takes WebKit's undivided 120. A touchpad or a precise
wheel moves by fractions of a notch and stays native, as do Shift, Ctrl / ⌘
(the zoom), a wheel over an inner element that can scroll itself and a
wheel at either end. Any other write to the scroller (a jump, a zoom holding its
anchor, the scrollbar, a key) ends the motion: each frame checks that
`scrollTop` still reads what the last frame wrote. The detail canvas
follows the motion like any scroll, and a 200 px glide stays inside its
margin on a desktop. A JS-driven scroll stops whenever the main thread
does, so the page work above (drawn in place, the text layer at rest) is
what keeps it moving. The `pdf: smooth wheel scrolling…` step checks a
notch passes through several positions when on, at scale 1 and 1.25, that
a spin of notches moves in every frame, and that off, a notch jumps once.

`tests/e2e/scenarios/pdfTouch.mjs` covers 400% and 800% rendering at DPR 2 under an
emulated canvas allocation limit: a detail canvas at 2 backing pixels per
CSS pixel over the part in view, every canvas inside the limits, the detail
canvas following a scroll, both canvases released for distant pages, one
supersampled canvas again below the cap, and the text layer and its
selection surviving a zoom. It also covers live ink, native Chromium touch
swipes with no mid-gesture offset writes, and the paper and ink colour in a
paper theme, which is what a doubled blend would change. The rendering cases
run in Playwright WebKit too (`GAMMA_E2E_BROWSER=webkit`, after
`npx playwright install webkit`) and pass there; the swipe and pinch steps
are Chromium-only, since they drive CDP touch events. A step sets the zoom
it starts from, since the steps before it differ by browser. One step turns
a 12-notch Ctrl+wheel train over a page with a highlight and its note. It
asserts that page 1 renders no time while the wheel turns and at least once
at rest, that no style is written on its box, text layer, highlight or
badge, and that the highlight and badge keep their place on the page. It
counts renders through a stand-in React DevTools hook that walks the fiber
tree after each commit (a page is React.memo's fiber, tag 15, over the
`.pdfPageWrap` div). That reads React internals, so re-check it on a React
upgrade; its positive controls make a broken counter fail, not pass.
`tests/pageRaster.test.mjs` and `tests/canvasSize.test.mjs` pin the raster
rules and the canvas bounds; other unit tests pin the snap timing,
cancellation and older-Safari fallback. Physical iPad GPU limits, render
times and momentum still need a device.

## A scan's text layer

A scanned paper is a picture of each page with an invisible text layer an
OCR program wrote over it (render mode 3). Old layers place their words
badly: the worst one met (*J. Phys. II* 2, 579, 1992, from HAL) sizes each
word from its own ink, 4 to 10 pt on one printed line, gives it a substitute
font's width, about 0.73 of the printed word, and writes it in the picture's
flipped pixel rows, which pdf.js's `TextLayer` draws a whole line above the
print. Selection and highlights come out patchy, find marks miss, and the
translation cuts a page into a hundred one-line blocks. The study behind the
repair, with its corpus of twelve scans from seven OCR producers and 3,809
born-digital pages, is [research/ocr.md](../research/ocr.md).

Every reader of a page's text goes through `pdf/pageText.js` (`pageText`):
the text layer and Ctrl+F in `PdfViewer.jsx`, the translation's
segmentation (`translateState.segmentNow`), the translation bench and the
figure tests. It reads the TextContent as before and, when the page is a
scan with an OCR layer, hands it back repaired (`repaired: true`); a page
the server recognized is built from its recognized words instead
([Recognized words](#recognized-words)). So what
a reader selects, what a search marks and what a translation sends are the
same words in the same places.

- **Which pages** (`pdf/scanDetect.js`). The rule: nearly all the glyphs the
  page shows are invisible (Tr 3 or 7, 90%) and the pictures it paints cover
  most of it (70%, their union: a page cut into strips counts whole). Only
  the operator list says either, and `getOperatorList` is a pass of its own
  through the page in the worker, images decoded, which the render does not
  share. So a `prescreen` on the TextContent comes first and passes a page
  only when its text shows a trace of a picture's measuring: most of it
  flipped, most of it stretched (Tz fitted to word boxes), many sizes with
  none dominant, or origins on a scan resolution's pixel grid. It missed none
  of the 418 scan pages it was run on (every page of ten scans and 98 of a
  scanned book's) and passed 4.9% of the born-digital ones, at 0.1 to 0.4 ms
  a page. A born-digital page it passes costs an operator list too: a median
  of about 7 ms, but up to 0.6 s on a page of many vector marks, which its
  text layer waits for. A page it passes is confirmed (`confirm`), unless the
  document's fonts already tell: an OCR layer's fonts draw only invisible
  text, so after one confirmed page `ScanMemo` answers for the pages drawn
  in the same fonts. A scanned book pays one or two operator lists (39 ms
  median on a scan, image decoding), not one a page. The confirm had no
  false positive on the born-digital set, LaTeXiT's invisible payloads
  included (no covering picture). A document's pages are decided one at a
  time, and only the verdict is kept, per page proxy (`knownScan`); the
  TextContent is read afresh each time, as before. The operator list leaves
  the page's decoded pictures on it, so Ctrl+F, which reads every page,
  lets a page more than two pages from the view go again (`page.cleanup()`),
  as a translation job's segmenting does.
- **The repair** (`pdf/scanText.js`, `repairTextContent`, 2 to 4 ms a page).
  Words are the selection string's, cut at white space, so what is selected
  and copied does not change. Each gets the band its matrix implies (a
  flipped one hangs down from its origin), and consecutive words chain into
  printed lines in stream order, never sorted: a new line where a word's band
  leaves the line's, where it starts before the word before it ends, or
  across a gap of three line heights (a column gutter inside an OCR line),
  the rule the server's rows follow too. Each line
  takes one band: the union of its words' bands where the boxes hug each
  word's ink (words of x-height letters drawn clearly shorter than those with
  ascenders), else their medians. Its words then tile it, each span running
  to where the next starts with the space folded in (`"word "`), the last
  one ending in `hasEOL`. Where boxes hug the ink, the cut is the middle of
  the gap; where a box is an em estimate it starts at its word, and the cut
  lies at most 0.35 line heights before the next box. A line's ends reach out
  by half its median gap, within a line height and the page's box
  (`page.view`). The items come out upright, `[h, 0, 0, h, x, baseline]`
  with the span the line's band, font ids kept (`pdfFonts`, `paperFace`), the
  styles' ascent and descent 0.8 and -0.2 so that `textRuns` reads the band
  `TextLayer` draws. A one-character item (a line ending in "a") is
  `[2h, 0, 0, h, …]`: `TextLayer` stretches a one-character span to its
  width only when its matrix is stretched by more than 1.5, and would
  otherwise draw it at a serif's width, short of its letter; every other
  reader takes the height from the matrix's second column and only the
  direction from its first. Rotated items pass through as pdf.js gave them,
  a run of them (a table on its side) keeping its own breaks; a break is
  added only where such a run meets a repaired line.
- **What it changes.** On the corpus's poor layers the share of words a
  span covers whole went from 26% to 98% and the translation's blocks per
  page from 88 to 40. On the good layers (Tesseract, CVISION, LuraDocument)
  the words' coverage held or improved, but one band a line is not always
  closer to the print than the layer's own: on the 1913 *Physical Review*
  (Tesseract) the ink rises above the band's top, and the lines whose band
  covers their ink went from 65% to 54%; the 1961 *Journal de Physique*
  went from 53 to 56 blocks a page. A paragraph of a repaired page is one block, and its
  requests carry `ocr` ([translation.md](translation.md#the-request)).
  Highlights made before keep their rectangles, now under the repaired
  spans; kept translations of a scan made before are looked up by text that
  no longer matches and stay unused. The server reads such a page's text in
  rows of words too ([documents.md](documents.md)).
- **What it cannot fix**, pixel-free: wrong letters ("Rksum4"), a layer
  drifting against its picture, a producer's baseline offset, scans with no
  layer at all. Those need the words read from the picture: Recognize text
  in this PDF (below).

### Recognized words

Once a stored PDF's scanned pages are recognized, on the server or in a
window ([documents.md](documents.md#recognized-text)), those pages read
the recognized words in place of their own layer, here as on the server.

- **The state** (`pdf/ocrState.js`, module state like `translateState`).
  `PdfViewer.jsx` registers each document as it commits it, under the
  stored file it is (`fileId` from App and PagePane, else the upload its
  URL names), and asks `GET /api/ocr/<doc>` afresh on every open. A
  folder's annotated-PDF export registers each paper it opens too, under
  its `doc_id` and with no URL (`folderTranslation.paperLayout`). The
  entry is per workspace and file. A page waits at most 3 s for that first
  answer, then reads its own layer until it comes. A failed answer reads as
  no recognized text, unless a state is already known.
- **The words.** `pageText` asks `ocrState.lines(page)` first. A recognized
  page's words come from `GET /api/ocr/<doc>/pages`, the page asked for and
  the recognized pages nearest it, at most 16 a request. They are held by
  the state's `stamp` in one store for all of the tab's documents, 800
  pages in all, the pages read longest ago going first, whichever document
  they are of. A new state drops its document's words of another stamp,
  and an answer of another stamp makes the tab read the state again.
- **The layout** (`pdf/ocrText.js`, pure). Each stored line is one band,
  never chained again by `scanText`'s rule, so the page's text is the
  server's: a line's words joined by a space, every line ending in
  `hasEOL`. The words tile their line through `scanText`'s own tiling
  (`bandLines`, `tileCuts`), the cut in the middle of each gap since the
  boxes hug the ink. Items are laid out upright in the page as shown, then
  each matrix is taken to user space through the inverse of the scale-1
  viewport's transform, so a rotated or cropped page reads its words where
  they are drawn. A one-character word is stretched `[2h, …]` as in the
  repair, except on a quarter-turned page, where `TextLayer` reads the
  stretch off terms the turn has zeroed. The font id is `gamma_ocr`, with
  ascent 0.8 and descent -0.2. The TextContent carries `repaired: true`
  and `ocr: true`, no operator list is built for the page, and
  `knownScan` is true for it.
- **A change.** The state's local revision only grows. The first answer
  changes only the pages a reader read from its own layer because the 3 s
  wait ran out (`ocrState.lines` records them); every other reader waited
  for it, so a translation seed or job that waited is neither stopped nor
  halted. After that, the revision moves on when the recognized pages or
  their stamp differ from what was announced. Each changed page records
  the revision it changed at (`pageRevision`). A page's text layer is
  built again when its own revision moves on (`PdfPage`'s `textRev`), the
  old layer staying until the new words are in. Ctrl+F's page texts are
  read again under a new revision, and the viewer's translation seed runs
  again. App's `docNonce` is `pdfDocNonce` plus
  `ocrState.useOcrNonce(ocrDocId(doc, pdfUrl))` (`pdfSource.js`: the
  document's stored file, else the upload its URL names), a count of the
  changes of the on-screen document's revision that never goes back, so a
  pinned search runs again for that document alone. The translated view
  hears which pages changed
  ([translation.md](translation.md#outliving-the-viewer)).
- **When it changes.** A recognition or a forget that ends, done, stopped
  or failed, makes every tab of the workspace that heard it end read the
  state again (`tasks/useAppJobs.js`). Other tabs hear it from their page
  socket: the server's `ocr` message ([collab.md](collab.md)) passes the
  page filter whichever page carries it, and App's `onOcr` refreshes that
  file, or the one on screen. For a message that names the file it also
  has the tasks store list at once and look out for the end of the file's
  OCR jobs (`tasks.expectEnd(ocrState.fileJobs(doc_id))`,
  [tasks.md](tasks.md#the-web-app-frontendsrctasks)). That way a run that
  started and ended between two listings is still reported. The socket
  also asks with no message on a hello after a dropped socket and on its
  poll rounds, which may have missed one. `refresh` always reads, and an answer that says nothing new
  announces nothing. A session's end clears every document's state
  (`ocrState.reset`, beside `endTranslations`) and ends this window's runs
  (`ocrRuns.reset`).
- **Reading in the window** (`pdf/ocrRuns.js`, `pdf/ocrTab.js`,
  `pdf/ocrLoop.js`, `pdf/ocrRaster.js`), where `/api/server-config`'s
  `ocr.where` is `window`. `ocrRuns` runs one scan at a time in a window,
  since each holds an engine of about 100 MB and a page's picture; a later
  start waits, the menu row's title then "Waiting for its turn…". tesseract.js
  7.0.0 is in `ocrTab.js`, a chunk of its own fetched by the first run
  ([frontend-refactor.md](frontend-refactor.md#lazy-boundaries)). Its
  worker script and its one core (the single-file SIMD LSTM build) are
  hashed assets of this build, and the models come from the server's
  `ocr.models`; `workerOptions` refuses any file of another origin, so
  nothing comes from a CDN. A browser without WebAssembly SIMD gets "This
  browser cannot recognize text: it lacks WebAssembly SIMD." The loop
  (`runWindow`, its server, engine and clock handed in):
  1. It starts the `ocr-tab` job through the tasks store (`POST
     /api/jobs/ocr` with `window: {doc_id, client, engine, langs, ver}`),
     follows it until it reads `recognizing`, then asks the plan (`GET
     /api/ocr/<doc>/plan`).
  2. It opens the stored file on its own, by range requests past
     `WHOLE_MAX_BYTES` (`openPdfDocument(url, {ranged: true})`), so the run
     goes on while the reader is elsewhere.
  3. Each page the plan names, one at a time: drawn as the server draws it
     for its engine (300 dpi, less past the plan's pixel and side limits
     or the device's canvas caps; intent `print`, which a hidden window
     still draws, without annotations), read back in strips of 256 rows to
     a grey PGM, the canvas released, recognized (page segmentation
     automatic, `user_defined_dpi` the canvas's), its TSV parsed into
     lines in 1/10000ths (`parseTsv`), and sent (`PUT
     /api/ocr/<doc>/pages/<n>`). A page past the plan's limits, or one
     that fails, goes as `{failed: true}`. The answer says which pages the
     server still wants.
  4. With none left, the job applies by itself. The window posts `POST
     /api/ocr/<doc>/done` only while the server still wants pages it cannot
     send (past the end of the document it opened, or sent already).

  Some failed requests wait until the window is shown and go again after
  1, 2, 4, 8 and 16 s: one with no answer, a proxy's 502, 503 or 504, the
  server's 503 that ends "— try again" (a page's words could not be kept
  just then), and a page's 429 from the rate limit. The start's 429 (the
  account's tasks are full) is not retried: the run fails at once with
  the server's reason. A 409 on the plan, a page or the done is judged
  by the job, by what the server says about how it ended:
  - The job is gone, failed as interrupted by a restart of the server (the
    apps' local server), or cancelled with `progress.idle` by its idle
    timer. The run starts the job again, at most 3 times, and the server
    skips the pages it staged.
  - The job is cancelled without `idle`, or still running with `stopping`
    set. A person stopped it, on any device, however long this window was
    away, and the run ends stopped.
  - The job is applying (`updating`) or done, and the run ends done.
  - Otherwise the refusal stands, such as another window taking the run
    over. The run fails with its sentence, and the job goes on without it.

  A failure on this window's side after the job started stops the job at
  once through the tasks store (`POST /api/jobs/<id>/cancel`). Such a
  failure is an engine that does not start, a document that does not
  open, or a request the server refuses outright (not a 409). Without the
  stop, the job would wait 10 minutes for pages. The pages sent wait for
  the next run, and the tab reports only that the run failed. A page's
  drawing and its reading have 120 s each, and the engine's start 180 s,
  counted only while the window is shown. A page that runs out goes as
  failed. A drawing that ran out is called off (`RenderTask.cancel`), so
  its canvas is freed at once, and a page whose time ran out while it
  waited behind a stalled one draws nothing once pdf.js gives it. An
  engine that ran out is started afresh, as it is every 50 pages.
  Tesseract's own messages go to its `debug_file`, the core's
  `/dev/null`, rather than to the console as errors.

  While a run goes, the pill says so on the channel `ocr:<docId>`
  (`ocrRuns.followPill`, from `useAppJobs`): "Reading the pages of
  “{title}” in this window: keep it open and in front until it is done. A
  stop keeps what was read." Its Stop ends the run. The screen is kept on
  meanwhile (`keepAwake` in `shared/lib/host.js`: the app shells get
  `{type: "keepAwake", on}`, [ipad.md](ipad.md#the-host-contract); a
  browser asks for the Screen Wake Lock, again each time the page is
  shown). The job's row in Background tasks shows the run on every
  device. Its Stop in this window ends the run here at once
  (`TASK_KINDS["ocr-tab"].halt`, `ocrRuns.jobStopped`), not at the run's
  next request. A run that waits its turn has no job yet. A row of this
  tab's own stands for it (kind `ocr-tab`, "Waiting for its turn…"), and
  its Stop calls the run off. At the run's turn the job's row takes over.
- **The Document menu rows** (`ocrState.useOcrMenu`), for a writer with an
  account (never a guest), outside share views, for a stored PDF.
  **Recognize text in this PDF** shows on a server whose `ocr` names a
  `where`, while `GET /api/ocr` counts pages a run would read (`todo`), a
  stopped run's pages wait to be applied (`staged`) or the last apply's
  steps are due (`pending`). Before a run has read which pages are scans,
  it shows while the server counts more scanned pages than it recognized
  or kept, or once `pageText` met a scan's layer or a page with no text
  (`noteScan`), and not once `good_layer` is said. With staged pages and
  nothing running it reads "Go on recognizing this PDF's text". On the
  window path its title adds "The pages are read in this window: keep it
  open and in front until it is done.", and a click starts the run in
  `ocrRuns`. A run of 50 pages or fewer on the server follows in the
  status pill. **Forget the recognized text…** (a trash icon, `danger`)
  shows while a page reads recognized words, needs no engine, and asks
  first ("Forget the recognized text?", `ocrState.forgetBox`); its Forget
  starts `POST /api/jobs/ocr-drop`. Both rows are disabled while an `ocr`,
  `ocr-tab` or `ocr-drop` job of the file waits or runs, or this window
  reads it, their title then saying it is being recognized or forgotten.
  There is one exception: the account's own `ocr-tab` job that is running
  and not stopping, for another window, while no run of the file goes
  here. The job's `params.window.client` is not this window's
  `ocrRuns.windowClient()`, so it belongs to a window reloaded since, or
  another one. The Recognize row is then offered as "Go on recognizing
  this PDF's text", and its title says the pages are read on in this
  window from the pages read, and that a window reading them now stops.
  A click starts the run here, and the server hands it that job
  (`window_take`, [documents.md](documents.md#recognized-text)). Known
  limit: after a take-over the job still names the window that started
  it, so that window's row stays disabled until the job ends.
- **The metadata popover** (`ocrState.textStatusTitle`): its text cell says
  when the text was recognized from the page pictures (`/api/pdf-text-status`'s
  `ocr`), and "Its text is being recognized from the page pictures — see
  Background tasks." while a run of the file goes. With `ocr` there and too
  little text (`ok` false) it says "The scanned pages were recognized from
  their pictures on {date}, but they hold little or no readable text." On
  a PDF without text it points to Recognize text only while the Document
  menu shows the row. The popover reads the status again when the file's
  revision moves on.
- **A replaced PDF** whose answer has `text_not_recognized`
  (`ocrState.replacedNote`): the status line adds "The new file's text is
  not recognized: choose Recognize text in this PDF in the Document menu."
  where the row would be offered (a writer with an account, a server that
  recognizes text, itself or in the window).

Tests: `tests/ocrText.test.mjs` (the shape, the tiling, a one-character
word, rotations and a crop box through real pdf.js viewports),
`tests/ocrState.test.mjs` (the chunks, revisions, the first answer, a
seed that waited for it, a late state rebuilding a page, failures, a stale
stamp, the bound on the words held, the menu rows and `good_layer`, the
window path, the go-on labels, another window's job taken over, the
forget row and its confirm, the popover, the replaced-file note, a
folder's export, App's search nonce and `fileJobs`),
`tests/scanText.test.mjs` (`pageText` with a fake state),
`tests/ocrRaster.test.mjs` (the zoom, the PGM, `parseTsv`, the dpi, the
same-origin options and `debug_file`, the SIMD probe, the engine string
held to the pin), `tests/ocrLoop.test.mjs` (the loop against a fake
server, engine and clock: retries, the start's 429, restarts, a Stop
however late it is seen, a Stop the job has not reached yet, the idle
end, a failure on the window's side, hung pages and their drawing called
off, the done POST), `tests/ocrRuns.test.mjs` (one run at a time, the
waiting run's row, stop, the job's Stop, a failure here stopping the
job, the pill, reset, the wake lock), and `tests/collabSession.test.mjs` /
`collabPoll.test.mjs` (`onOcr`).

## Load phases

The viewer reports each phase to the host (`onLoadState`), which drives the
status pill, stamps `performance.mark("pdf-<phase>", {detail: {url, ms}})`
and writes `pdf <phase> +<ms>` to the session log (Settings → Help & diagnostics). `ms`
counts from the `open` phase of that url.

| Phase | Meaning |
|---|---|
| `open` | the viewer started opening this url (the clock starts) |
| `layout` | skeleton page boxes from the manifest are in the DOM (pre-paint; the pending restore lands here, measured at the `scale` the phase carries) |
| `start` / `progress` / `done` | a whole-file download, with byte progress |
| `cached` | bytes came from memory / IndexedDB, or the document from `DOC_CACHE` |
| `parsing` | `getDocument` called (range open: pdf.js is fetching its chunks) |
| `opened` | pdf.js has the document: xref and page tree parsed |
| `measuring` | the fallback page measure, when there is no manifest |
| `rendered` | the document's pages are in the DOM (pre-paint; the pending restore lands here when there was no skeleton, measured at the `scale` the phase carries) |
| `painted` | the first canvas painted, or the pages came back with the pixels they had (with `rendered`); the pill clears |
| `error` / `cancelled` | failed with `detail` / the url changed mid-load |

## Measured

The e2e timing probe (`frontend/tests/e2e/scenarios/pdfload.mjs`; `npm run
e2e -- --only "pdf load"`): a 300-page, 20 MB document (tiny pages plus a
padding stream, so the file weighs what a scanned book weighs), Chromium at an
emulated 20 Mbps / 40 ms latency, first paint measured from `open`. The
timing steps assert only that the document paints; the numbers are their
notes. The same scenario then pins the behaviours: a landscape page far below
the fold has its own box shape (only the manifest could have said so), the
last-read page comes back after a reload, two large documents alternate
through the parsed-document cache without mixing, a paper's tab left for a
notes page comes back drawn in every frame, where it was left and at its
zoom, round after round (in a browser whose scrollbar takes room), so does
each of two papers' tabs read at different zooms, with no "Rendering
page…", and an anonymous share
visitor opens the large document by ranges with no request rejected (the
share token rides on the ranges, the manifest and the HEAD).

| Open | Before | After (four runs) | On the wire |
|---|---|---|---|
| Cold (nothing cached, first visit) | 9.73 s | 0.68 to 0.79 s; page boxes at 65 to 78 ms; 0.46 s with the worker precompressed (one run); 0.50 to 0.54 s with pdf.js a chunk of its own (four runs, page boxes at 65 to 71 ms) | 0.3 MB in 2 range requests, plus the worker script once per browser: 1.3 MB (0.6 s of the total) as it is, 0.3 MB (0.16 s) as its Brotli copy; pdf.js itself, 0.1 MB, comes with the app's startup requests |
| Warm (new tab: IndexedDB + HTTP cache) | 1.01 s | 0.13 to 0.14 s | nothing |
| Same tab, back from the library (`DOC_CACHE`) | not measured before | 0.04 s (pages in the DOM at 8 ms) | nothing |

Where the time went before: the whole 20 MB downloaded before anything was
parsed (about 8 s at 20 Mbps), then the worker script again, then eight serial
page measurements. The backfill of the whole file lands about 11.7 s after the
cold paint (3 s delay plus the 20 MB), which is what makes the warm row warm.

Not done, and why: a server-rendered preview JPEG per page (the upstream
fork's `/api/page-image`) would cover image-heavy scans, where pdf.js itself
renders slowly; for the documents this library holds, the numbers above leave
nothing for it to hide, so it stays out until a real scan says otherwise.
MRC flattening (rewriting scans into a second upload) was rejected in the
research note.
