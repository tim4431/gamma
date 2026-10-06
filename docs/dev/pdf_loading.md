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
built from it is exact). It is derived data next to the PDF text index, so it
is created with `CREATE TABLE IF NOT EXISTS` (no migration step), lives in
backups harmlessly, and is purged with the index when no page carries the
document any more (`pdf_index.purge_unused`).

Who writes it: `storage.store_pdf` (uploads, imports, file chips) and
`storage.store_pdf_path` (an upload sent in parts, `upload_parts.finish`;
[api.md](api.md) "/uploads/parts"), the `/api/pdf` proxy's save path and
`/api/clip` schedule it on a background thread the moment the file lands, so
the first open finds it ready; the search indexer computes it while it has
the file open anyway; and the endpoint computes it on demand for anything
older, in pdfium, in FastAPI's threadpool (the route is a sync `def`). A walk
already running for the document is joined, not repeated, so opening a book
right after uploading it waits for the upload's own walk instead of queueing
a second one behind the pdfium lock.

Every manifest walk is `pdf_text.page_sizes`, behind the same lock as text
extraction. The lock is taken per page (and once each for the open and the
close), not for the whole document, so a manifest walk runs between the
pages of a book the search indexer is reading. Every page it opens is closed
explicitly, inside the lock: pypdfium2 objects sit in reference cycles, so a
page merely dropped would be closed by the cyclic GC later, on another
thread, outside the lock, which crashes the server (`pdf_text.py` has the
story; its `_serialize_finalizers` is the net). A file pdfium cannot read is
stored with `pages: 0` so it is not parsed again on every open. The endpoint
sends that answer `no-store` and a real one with a day of `private` caching
(a doc id is a content hash, its manifest never changes).

Access is the file's own rule: a workspace member, or a share token confined
to the shared page's document (`_share_can_read_upload`). The check reads the
shared pages only and remembers a yes for a few minutes, so a PDF opened by
range requests asks once, not per chunk.

## The client (`src/pdf/PdfViewer.jsx`, `src/pdf/pdfSource.js`)

`pdf/pdfSource.js` holds the pure decisions, unit-tested in
`tests/pdfSource.test.mjs`: which URL is an upload (`docIdOf`), which
transport to use (`chooseTransport`), the pdf.js options of a range open
(`rangeOpenOptions`), and how a manifest becomes a page layout
(`layoutFromManifest`, refusing anything that does not describe a readable
document). The viewer's load effect composes them:

1. **A parsed document from a recent visit** (`DOC_CACHE`, the last two
   `PDFDocumentProxy` objects with their layout) is committed as it is.
   Documents are never destroyed on a tab switch or a viewer unmount, only on
   eviction, a second after the commit so mounted pages are not torn out from
   under pdf.js. The byte cache (`PDF_CACHE`) holds one entry, which pays
   for this one: re-reading bytes from IndexedDB costs tens of milliseconds,
   re-parsing them is the expensive half.
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
4. **Layout from the manifest, or measured.** When the manifest describes
   the opened file (same page count) its sizes are used and the eight-page
   measure loop and the 50-page background refinement are skipped, which also
   removes the layout shifting under a scroll restore that the refinement
   caused. Without a manifest (proxy URLs, a failed read) the old path
   measures and refines exactly as before. Bytes from the cache wait at most
   250 ms for the manifest; measuring is still correct, only slower.

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

`shared/lib/canvasSize.js` bounds every PDF and live-ink backing store to 8 Mi pixels
and 4096 pixels per edge. WebKit documents both a
[canvas area limit](https://bugs.webkit.org/show_bug.cgi?id=171238) and
[total canvas allocation failures](https://bugs.webkit.org/show_bug.cgi?id=195325),
and an iPad may report a Mac user agent, so the cap applies everywhere.
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
- **Detail mode past that.** For A4 the cap leaves about 4.09 / zoom backing
  pixels per CSS pixel, so on a DPR 2 screen detail mode starts near 205% and
  on a DPR 1 screen just past 400%. The zoom limit is 800%
  (`shared/model/zoom.js`); the detail canvas costs the same there as at
  400%, since it is sized by the viewport. The whole-page canvas
  (the base) drops to half its capped linear resolution and becomes a
  preview. A second canvas (the detail) covers the page's part of one render
  window, at the device pixel ratio.
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
  the base and sharpens on rest.
- **Zooming.** Both canvases are sized in percent of the page box, so they
  stretch with it in the same commit. The redraw starts 150 ms after the
  last zoom step (`ZOOM_SETTLE_MS`), the part in view first, then the base.
  A first paint does not wait.
- **The text layer is built once per page**, after the page's first pixels,
  and kept across zooms and when the page scrolls away. pdf_viewer.css sets
  `--scale-factor: 1` on `.pdfViewer`, so the layer is laid out at scale 1
  and `PdfPage` scales it with a CSS transform. After a zoom settles
  `TextLayer.update` re-measures the spans at the new size. A text selection
  and the citation marks survive a zoom.
- **One renderer.** Every raster goes through `renderRegion`: the base, the
  detail canvas, the highlight capture (`captureRef`) and the area-note
  snapshot (`cropPage`). The last two are drawn from the document, not copied
  off the screen, where the bitmap may be a preview. The transform is passed
  to pdf.js as its `transform` parameter. pdf.js paints the paper before
  applying its own transform, so a context translated beforehand leaves the
  crop without paper.

`.pdfPageCanvas` is a wrapper element around the two canvases. The theme
treatments (multiply and soft-ink opacity in the paper themes, the invert
filter of the dark page) sit on the wrapper, which composites the canvases
before blending. On the canvases themselves the overlap would be blended
twice.

`PdfPage`'s intersection observer is rooted at the PDF scroller with 900 CSS
pixels of look-ahead. A page outside it releases both backing stores
(`show(null)`) and keeps its geometry, text and overlays; it repaints on
return without waiting, and a forced render (jumps, the cited page) still
works. A page inside the look-ahead but outside the render window holds only
its base. Release and unmount cancel the pending pdf.js renders.

`pdf/verticalScrollSnap.js` is the always-on one-finger vertical alignment
(`installVerticalScrollSnap`, reinstalled on zoom and document changes). It
judges direction after 8 CSS pixels within a 30° vertical cone. It never
writes scroll offsets while a finger or native momentum is moving. After
`scrollend` (250 ms of quiet after lift on browsers without it) it corrects
horizontal drift once, instantly under reduced motion. A diagonal start, a
deliberate sideways turn, a second contact, a cancelled gesture, keyboard or
wheel input, a zoom change or teardown drops the pending alignment. Writing
offsets mid-gesture fights WebKit's native scroll animation
([WebKit issue](https://bugs.webkit.org/show_bug.cgi?id=255193)).

`tests/e2e/scenarios/pdfTouch.mjs` covers 400% and 800% rendering at DPR 2 under an
emulated canvas allocation limit: a detail canvas at 2 backing pixels per
CSS pixel over the part in view, every canvas inside the limits, the detail
canvas following a scroll, both canvases released for distant pages, one
supersampled canvas again below the cap, and the text layer and its
selection surviving a zoom. It also covers live ink, native Chromium touch
swipes with no mid-gesture offset writes, and the paper and ink colour in a
paper theme, which is what a doubled blend would change. The rendering cases
are written to run in Playwright WebKit too (`GAMMA_E2E_BROWSER=webkit`);
the detail-canvas assertions have only been run in Chromium.
`tests/pageRaster.test.mjs` and `tests/canvasSize.test.mjs` pin the raster
rules and the canvas bounds; other unit tests pin the snap timing,
cancellation and older-Safari fallback. Physical iPad GPU limits, render
times and momentum still need a device.

## Load phases

The viewer reports each phase to the host (`onLoadState`), which drives the
status pill, stamps `performance.mark("pdf-<phase>", {detail: {url, ms}})`
and writes `pdf <phase> +<ms>` to the session log (Settings → Help & diagnostics). `ms`
counts from the `open` phase of that url.

| Phase | Meaning |
|---|---|
| `open` | the viewer started opening this url (the clock starts) |
| `layout` | skeleton page boxes from the manifest are in the DOM (pre-paint; the pending restore lands here) |
| `start` / `progress` / `done` | a whole-file download, with byte progress |
| `cached` | bytes came from memory / IndexedDB, or the document from `DOC_CACHE` |
| `parsing` | `getDocument` called (range open: pdf.js is fetching its chunks) |
| `opened` | pdf.js has the document: xref and page tree parsed |
| `measuring` | the fallback page measure, when there is no manifest |
| `rendered` | the document's pages are in the DOM (pre-paint; the pending restore lands here when there was no skeleton) |
| `painted` | the first canvas painted; the pill clears |
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
through the parsed-document cache without mixing, and an anonymous share
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
