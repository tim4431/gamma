# A fast wheel spin under smooth scrolling

The question: with the smooth wheel setting on (Settings → Reading › PDFs,
`shared/lib/smoothWheel.js`), a fast spin of the wheel did not glide. It
stuck for a while and then jumped. What held it, and what else does a fast
scroll cost the viewer? Measured in October 2026; the mechanics now in force
are in [dev/pdf_loading.md](../dev/pdf_loading.md#high-zoom-and-touch-scrolling).

## How it was measured

An isolated server (the e2e harness) with a real paper (a 22-page Nature
article, 13.7 MB, figures on most pages), driven by headed Chromium on the
developer's machine (RTX 4070 SUPER, DPR 2), window 1920 × 1080, the Notes
panel open, so the viewer pane is 1046 × 727 CSS px and fit-width draws the
page 1046 px wide (about 170%). Below the canvas cap: one 2091 × 2779 canvas
per page, no detail mode. During the measurement the window sat on a display
reporting 32 Hz (a Remote Desktop adapter), so an idle frame was 31 ms; the
counts below are per frame, whatever the rate.

The spin: 30 wheel notches of 200 px, 40 ms apart (Playwright's
`mouse.wheel`, the discrete notch), from the top of the document. Recorded
per run: `scrollTop` at every animation frame and the time of every wheel
event; long animation frames with script attribution; a CPU profile
symbolicated through the build's source maps; a Chrome trace, summed per
thread as self time. The probe is a one-off script against the harness, not
in the suite.

## Finding 1: a notch stood still for a frame, and a spin is all notches

Chromium delivers a wheel event inside the frame it belongs to, just before
that frame's `requestAnimationFrame` callbacks, and stamps those callbacks
with the frame's start, which is earlier than the event. The glide measured
its progress from `performance.now()` at the event, so its first frame found
no time passed and wrote back the position it had started from. One frame of
standing still per notch; in the spin, 30 of the 63 frames moved nothing (29
of them were frames whose timestamp preceded the notch they carried). On the
screen that is a glide at half the frame rate with doubled steps, and a
faster spin, a notch to every frame, stands still for as long as it lasts.

Built: the glide is timed by the frame clock. Every frame closes a fixed
share of what is left (`approach`: 95% of a notch's distance in 150 ms, the
first frame after rest counted as 16 ms), and a notch on the way only moves
the goal, so nothing is ever reset. Same spin: 75 frames, none without
movement, no frame gap over the display's own 32 ms.

## Finding 2: what each page costs the glide

A JS-driven scroll stops whenever the main thread does, where a native scroll
keeps moving on the compositor. So every long task the pages cause while
scrolling is a visible stall under the setting, and worth less than it was.

| Main thread, before | Over the spin | Notes |
|---|---|---|
| `put` (the copy of a rendered page onto its on-screen canvas) | 63 ms in 3 calls: 7, 20 and 33 ms | the browser records what pdf.js draws and rasterizes the record when the canvas is first used; drawn outside the document, that was the copy, in one frame |
| pdf.js painting (`executeOperatorList`, chunks of up to 15 ms) | 67 ms | on the main thread by design |
| the text layer (`measureText`, `fillText`, span layout and text shaping) | about 100 ms: 40 ms of script, 36 ms of layout, 20 ms of shaping | built for pages the spin scrolled past |
| hit tests | 18 ms | Chromium's hover update after each scroll |

Built: a page is drawn into the hidden twin of its on-screen canvas, which
is in the document, and the two are swapped once the render lands
(`pair` in `pdf/pageRaster.js`). The record is then rasterized frame by
frame beside pdf.js's chunks and nothing is copied; the same holds for the
detail canvas at high zoom. The text layer waits until the scroller has sent
no scroll event for 100 ms (`afterScrollRest`), so a spin builds none and
the pages near the view are built when it rests.

| Spin, smooth wheel on | Before | After |
|---|---|---|
| frames without movement | 30 of 63 | 0 of 75 |
| long tasks (over 50 ms) | 2: 68 and 55 ms | none |
| longest animation-frame callback | 54 ms (a page's copy) | 16 ms |
| frames the compositor presented | 41 in 2.3 s, median 61 ms apart | 73 in 2.6 s, median 31 ms apart |
| main thread busy | 627 ms of 2290 | 522 ms of 2644 |
| layout, text shaping, paint, layer update | 36, 20, 26, 23 ms | 18, 6, 12, 11 ms |

The raster flush itself (`RasterCHROMIUM`, about 90 ms over the spin) did
not shrink; it is spread over the frames that draw instead of landing in
one. With the setting off the browser jumps, as before: 32 of 63 frames
without movement, which is the notches themselves.

## Still open

- **pdf.js paints on the main thread.** A chunk of a page's operator list
  (up to 15 ms) plus its share of the flush still lands in the glide's
  frames, over budget at 60 Hz. Rendering in a worker with `OffscreenCanvas`
  would take it off the main thread altogether; not tried.
- **Pages a spin scrolls past are rendered anyway.** The 900 px look-ahead
  renders every page it reaches; a spin faster than a page a second could
  render only what is in view and leave the look-ahead for when it slows.
- **The glide's tail.** Closing a share of the remainder per frame ends
  about 265 ms after the last notch (the last 100 ms move under 5 px), so a
  scroll rests later than it did, which delays the detail redraw and the
  text layer by that much.
- **Measured at 32 Hz.** The physical 4K display runs at 60 Hz; the counts
  per frame hold, the per-frame budget there is half.
