// How a PDF page gets its pixels. docs/dev/pdf_loading.md ("High zoom and
// touch scrolling") has the reasoning, docs/research/pdf-zoom-rendering.md
// the measurements behind it.
//
// A page is one canvas over the whole page for as long as that canvas can
// match the screen. shared/lib/canvasSize.js caps every canvas, so past some
// zoom it cannot. From there the whole-page canvas (the base, still at its
// capped resolution) is what shows past the view, and a second canvas (the
// detail) covers just the part of the page near the view, at the screen's
// own resolution. The page box carries the zoom; the bitmaps only ever cover
// what the screen can show.
//
// The rules come first, pure and unit-tested (tests/pageRaster.test.mjs);
// the drawing follows.
import { canvasRatio, canvasSize, CANVAS_MAX_EDGE, CANVAS_MAX_PIXELS } from "../shared/lib/canvasSize.js";
import { RenderQueue, RENDER_CANCELLED } from "./renderQueue.js";

// A zoom stretches the bitmaps it has and redraws this long after its last
// step, so a wheel train or a held zoom button rasters once.
export const ZOOM_SETTLE_MS = 150;
// A detail render that scrolling outran is not restarted until the scroller
// has been still this long.
const SCROLL_QUIET_MS = 120;
// The detail canvases reach past the viewport by at most this share of its
// size on each side, and are redrawn when less than LEAD of that reach is
// left ahead of the view.
const MARGIN_MAX = 0.5;
const LEAD = 0.25;
// Rounding a detail canvas onto its pixel grid moves its edges by a pixel or
// so; that much short is still covered.
const SLACK = 2; // CSS px
// The look-ahead: PdfPage renders a page this close to the view (the margin
// of its IntersectionObserver).
export const RENDER_MARGIN_PX = 900;
export const RENDER_MARGIN = `${RENDER_MARGIN_PX}px`;
// Renders of pages away from the view wait while the scroller moves faster
// than this many viewport heights a second, over the last MOTION_WINDOW_MS.
export const FAST_VIEWS_PER_S = 2;
const MOTION_WINDOW_MS = 120;
// Past the look-ahead a page keeps the pixels it has, as far out as
// KEEP_MARGIN (PdfPage's observer) and within KEEP_BUDGET_BYTES of canvas
// over the whole document, so a scroll back over read pages redraws nothing.
// Only on an engine with the roomy canvas limits: a touch-first device or
// WebKit releases a page as soon as it leaves the look-ahead, as before.
const ROOMY = CANVAS_MAX_PIXELS >= 16 * 1024 * 1024;
export const KEEP_MARGIN = ROOMY ? "300% 0px" : RENDER_MARGIN;
export const KEEP_BUDGET_BYTES = ROOMY ? 256 * 1024 * 1024 : 0;

// Canvas pixels per CSS pixel that count as sharp on this screen. Past 3
// nothing is gained.
const screenRatio = (dpr) => Math.min(3, dpr > 0 ? dpr : 1);

// The whole-page canvas of a cssW × cssH page box. While the limits leave it
// at the screen's resolution it is supersampled as far as they allow and is
// all the page needs. Once they do not, `detail` is set. The base keeps all
// the limits allow either way: a wheel notch can carry the view past the
// detail canvas, and until the detail redraw lands that strip shows the base.
// At half resolution the strip was visibly blurry and flashed sharp a frame
// later on every notch.
export function basePlan(cssW, cssH, dpr) {
  const sharp = screenRatio(dpr);
  const ratio = canvasRatio(cssW, cssH, Math.max(2, sharp));
  return { ...canvasSize(cssW, cssH, ratio), detail: ratio > 0 && ratio < sharp };
}

// How far past the viewport the detail canvases reach, as a share of the
// viewport's size on each side: what the canvas limits leave once the
// viewport itself is covered, up to MARGIN_MAX. Both limits count. A long
// narrow viewport (a phone held upright) runs into the edge limit well before
// the area one, and a margin past it would cost the whole canvas its
// sharpness.
export function windowMargin(viewW, viewH, dpr) {
  if (!(viewW > 0 && viewH > 0)) return 0;
  const sharp = screenRatio(dpr);
  // linear room over the bare viewport: 1 is no margin at all
  const room = Math.min(Math.sqrt(CANVAS_MAX_PIXELS / (viewW * viewH * sharp * sharp)),
    CANVAS_MAX_EDGE / (Math.max(viewW, viewH) * sharp));
  return Math.max(0, Math.min(MARGIN_MAX, (room - 1) / 2));
}

// The part of a pageW × pageH page box inside the viewport grown by `margin`.
// `view` is the viewport in the page box's own CSS px, so it can start before
// 0 and end past the page. null when the two do not meet.
export function windowSlice(pageW, pageH, view, margin) {
  const mx = view.width * margin, my = view.height * margin;
  const x = Math.max(0, view.x - mx), y = Math.max(0, view.y - my);
  const right = Math.min(pageW, view.x + view.width + mx), bottom = Math.min(pageH, view.y + view.height + my);
  return right > x && bottom > y ? { x, y, width: right - x, height: bottom - y } : null;
}

// The detail canvas for a slice of the page box: its backing size, and the
// rectangle it really covers. That is the slice moved onto the canvas's own
// pixel grid, counted from the page's corner, so the bitmap sits on the same
// grid as the page box instead of being resampled half a pixel off it.
export function detailPlan(slice, dpr) {
  const ratio = canvasRatio(slice.width, slice.height, screenRatio(dpr));
  const { width, height } = canvasSize(slice.width, slice.height, ratio);
  return {
    width, height,
    rect: { x: Math.round(slice.x * ratio) / ratio, y: Math.round(slice.y * ratio) / ratio, width: width / ratio, height: height / ratio },
  };
}

// Whether a drawn rectangle still covers a needed one.
export function covers(drawn, need) {
  return drawn.x <= need.x + SLACK && drawn.y <= need.y + SLACK
    && drawn.x + drawn.width >= need.x + need.width - SLACK
    && drawn.y + drawn.height >= need.y + need.height - SLACK;
}

// One raster of a page into a width × height canvas, `canvas` (resized, so
// cleared) or a new one: `rect`, a rectangle of the page box at `scale` in
// CSS px, or the whole page without one. Every render of a page goes through
// here. The transform is handed to pdf.js rather than set on the context:
// pdf.js paints the paper before it applies its own, so a context that is
// already translated gets paper in the wrong place and none where the crop
// is.
export function renderRegion(page, { scale, rect, width, height, annotationMode, canvas = document.createElement("canvas") }) {
  const viewport = page.getViewport({ scale });
  const r = rect || { x: 0, y: 0, width: viewport.width, height: viewport.height };
  canvas.width = 0; canvas.height = height; canvas.width = width;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("PDF canvas allocation failed");
  const kx = width / r.width, ky = height / r.height;
  const task = page.render({ canvasContext: ctx, viewport, transform: [kx, 0, 0, ky, -r.x * kx, -r.y * ky], annotationMode });
  return { canvas, task };
}

// A PNG data URL of one rectangle of a page: what an area note and a chat
// attachment show. `rect` {x1, y1, x2, y2} is measured in a page box of
// box.width × box.height, whatever zoom that was. Drawn from the document,
// not copied off the screen: at 2× the page's own size, more for a small
// crop, less where a large one would pass the canvas limits.
export async function cropPage(page, rect, box, annotationMode) {
  const own = page.getViewport({ scale: 1 });
  const kx = own.width / (box.width || own.width), ky = own.height / (box.height || own.height);
  const w = Math.max(1, (rect.x2 - rect.x1) * kx), h = Math.max(1, (rect.y2 - rect.y1) * ky);
  const s = canvasRatio(w, h, Math.min(4, Math.max(2, 1200 / w)));
  const { canvas, task } = renderRegion(page, {
    scale: s, rect: { x: rect.x1 * kx * s, y: rect.y1 * ky * s, width: w * s, height: h * s },
    ...canvasSize(w, h, s), annotationMode,
  });
  try {
    await task.promise;
    return canvas.toDataURL("image/png");
  } finally {
    canvas.width = 0; canvas.height = 0;
  }
}

const release = (canvas) => { canvas.width = 0; canvas.height = 0; };
const bytesOf = (canvas) => canvas.width * canvas.height * 4;

// What the pages of one scroller share: the render queue
// (pdf/renderQueue.js) fed with the scroller's speed, and the pages holding
// pixels, kept to the memory budget.
const SHARED = new WeakMap();
function sharedFor(scroller) {
  let shared = SHARED.get(scroller);
  if (shared) return shared;
  const samples = []; // [time, scrollTop] over the last MOTION_WINDOW_MS
  let timer = 0, frame = 0;
  const fast = () => {
    const now = performance.now();
    while (samples.length && now - samples[0][0] > MOTION_WINDOW_MS) samples.shift();
    if (samples.length < 2) return false;
    const [t0, y0] = samples[0], [t1, y1] = samples[samples.length - 1];
    return t1 > t0 && Math.abs(y1 - y0) / (t1 - t0) * 1000 > scroller.clientHeight * FAST_VIEWS_PER_S;
  };
  const queue = new RenderQueue({ fast });
  const poke = () => { frame = 0; queue.poke(); };
  scroller.addEventListener("scroll", () => {
    samples.push([performance.now(), scroller.scrollTop]);
    frame ||= requestAnimationFrame(poke);
    // Once the scroll stops the speed reads zero, and what waited for that starts.
    clearTimeout(timer); timer = setTimeout(poke, MOTION_WINDOW_MS + 20);
  }, { passive: true });
  const holders = new Set(); // {bytes, distance, pinned, release}
  // Over the budget, the pages farthest from the view let go first; never
  // one inside the look-ahead, nor one rendered on request.
  const trim = () => {
    let total = 0;
    for (const h of holders) total += h.bytes();
    if (total <= KEEP_BUDGET_BYTES) return;
    const far = [...holders].map((h) => [h, h.distance()]).filter(([h, d]) => d > RENDER_MARGIN_PX && !h.pinned()).sort((a, b) => b[1] - a[1]);
    for (const [h] of far) {
      if (total <= KEEP_BUDGET_BYTES) break;
      total -= h.bytes();
      h.release();
    }
  };
  shared = { queue, holders, trim };
  SHARED.set(scroller, shared);
  return shared;
}

// Two canvases in one slot: one shows, the other is drawn into and then
// shown in its place. The browser records what pdf.js draws and rasterizes
// the record the first time the canvas is used. For a canvas outside the
// document that was the copy onto the screen: a page's whole record in one
// frame, 20 to 33 ms for a page with figures, which held a wheel glide
// still (docs/research/pdf-wheel-spin.md). A canvas in the document, even a
// hidden one, is rasterized as it is drawn, frame by frame beside pdf.js's
// own chunks, and nothing is copied. The hidden one is drawn into, so the one
// on screen never shows a half-painted page, and it gives up its backing
// store once it is replaced. The hidden one carries `data-spare`.
function pair(front) {
  const back = front.cloneNode(false);
  back.style.display = "none";
  back.dataset.spare = "";
  front.after(back);
  const p = {
    front, back,
    show() {
      [p.front, p.back] = [p.back, p.front];
      p.front.style.display = "block"; delete p.front.dataset.spare;
      p.back.style.display = "none"; p.back.dataset.spare = "";
      release(p.back);
    },
    hide() { p.front.style.display = "none"; },
    clear() { release(p.front); release(p.back); },
  };
  return p;
}

// Keeps one page's canvases showing the right pixels. `wrap` is the page
// box, `base` and `detail` the canvases inside it (each gets a twin, see
// `pair`); the scroller is the .pdfViewer around them.
//   show(page, {scale, annotationMode, pinned})  what they should show. A
//       first paint starts at once; over a bitmap of the same page (a zoom)
//       it waits for the zoom to settle, and CSS stretches what is there.
//       `pinned`: rendered on request (a jump, the cited page), never
//       trimmed for memory.
//   show(null)   release all: a page far from the view keeps no backing store.
//   onPainted(page)  fresh pixels of `page` landed.
//   isCancel(error)  tells pdf.js's cancellation from a failure; the caller
//       owns the pdf.js module.
export function installPageRaster({ wrap, base: baseEl, detail: detailEl, onPainted, isCancel }) {
  const scroller = wrap.closest(".pdfViewer");
  const shared = sharedFor(scroller);
  const base = pair(baseEl), detail = pair(detailEl);
  let want = null;        // {page, scale, annotationMode, pinned, width, height}; width × height is the page box in CSS px
  let shown = null;       // the `want` the base canvas shows
  let detailShown = null; // {want, rect}: what the detail canvas shows, rect in CSS px of that page box
  let baseTask = null;    // renders in flight
  let detailJob = null;   // {task, rect}
  let settle = 0, quiet = 0, frame = 0; // a zoom settling; a scroll going quiet; a coalesced view change
  let outrun = false;     // scrolling cancelled the last detail render, and none has landed since
  let resized = null;     // the scroller's ResizeObserver, while the view is followed

  const dpr = () => window.devicePixelRatio || 1;
  const report = (e) => { if (want) console.error("PdfPage render error:", e); };
  // How far the page box is from the view, vertically, in CSS px; 0 touching it.
  const distance = () => {
    const p = wrap.getBoundingClientRect(), s = scroller.getBoundingClientRect();
    const top = s.top + scroller.clientTop;
    return Math.max(0, top - p.bottom, p.top - (top + scroller.clientHeight));
  };
  const holder = { distance, bytes: () => bytesOf(base.front) + bytesOf(detail.front), pinned: () => !!want?.pinned, release: () => show(null) };
  // Fresh pixels landed: the page holds them, within the budget.
  const held = () => { shared.holders.add(holder); shared.trim(); };

  // The viewport in the page box's CSS px. Measured from the boxes on screen
  // and scaled back, so it holds under the pinch preview's transform too.
  function view() {
    const p = wrap.getBoundingClientRect(), s = scroller.getBoundingClientRect();
    const k = p.width > 0 ? want.width / p.width : 1;
    return {
      x: (s.left + scroller.clientLeft - p.left) * k, y: (s.top + scroller.clientTop - p.top) * k,
      width: scroller.clientWidth * k, height: scroller.clientHeight * k,
    };
  }

  // Waits a render out. False when it was cancelled or is no longer wanted.
  async function settled(task, target) {
    try {
      await task.promise;
    } catch (e) {
      if (e === RENDER_CANCELLED || isCancel(e) || want !== target) return false;
      throw e;
    }
    return want === target;
  }

  // A render that was called off leaves the hidden canvas to the next one,
  // which resizes it; only when none followed is the canvas released.
  async function drawBase(target, plan) {
    const task = shared.queue.enter({
      distance,
      start: () => renderRegion(target.page, {
        scale: target.scale, width: plan.width, height: plan.height, annotationMode: target.annotationMode, canvas: base.back,
      }).task,
    });
    baseTask = task;
    try {
      if (!(await settled(task, target))) { if (baseTask === task) release(base.back); return; }
      base.show();
      shown = target;
      held();
      onPainted?.(target.page);
    } finally {
      if (baseTask === task) baseTask = null;
    }
  }

  function dropDetail() {
    detailJob?.task.cancel();
    detailJob = null;
    clearTimeout(quiet); quiet = 0;
    outrun = false;
    detail.clear();
    detail.hide();
    detailShown = null;
  }

  async function drawDetail() {
    const target = want;
    const v = view();
    const slice = windowSlice(target.width, target.height, v, windowMargin(v.width, v.height, dpr()));
    if (!slice) { dropDetail(); return; }
    const plan = detailPlan(slice, dpr());
    detailJob?.task.cancel();
    const task = shared.queue.enter({
      distance,
      start: () => renderRegion(target.page, {
        scale: target.scale, rect: plan.rect, width: plan.width, height: plan.height, annotationMode: target.annotationMode, canvas: detail.back,
      }).task,
    });
    const job = detailJob = { task, rect: plan.rect };
    try {
      if (!(await settled(task, target))) { if (detailJob === job) release(detail.back); return; }
      // In percent of the page box: a zoom stretches it with the page until
      // the redraw lands, like the base.
      const { rect } = plan, { style } = detail.back;
      style.left = `${rect.x / target.width * 100}%`; style.top = `${rect.y / target.height * 100}%`;
      style.width = `${rect.width / target.width * 100}%`; style.height = `${rect.height / target.height * 100}%`;
      detail.show();
      detailShown = { want: target, rect };
      outrun = false;
      held();
      onPainted?.(target.page);
    } finally {
      if (detailJob === job) detailJob = null;
    }
  }

  // Scrolling or a resized pane moved the view over a page with a detail canvas.
  function onView() {
    frame = 0;
    if (!want || settle) return; // a settling zoom redraws everything itself
    const v = view();
    const margin = windowMargin(v.width, v.height, dpr());
    // Out of the window altogether: nothing to keep sharp, and no pixels kept for it.
    if (!windowSlice(want.width, want.height, v, margin)) { dropDetail(); return; }
    const need = windowSlice(want.width, want.height, v, margin * LEAD);
    if (!need) return;
    if (detailShown?.want === want && covers(detailShown.rect, need)) return;
    if (detailJob && covers(detailJob.rect, need)) return;
    // The render in flight is for a view that has moved on. Starting another
    // on every frame of a fast scroll would only cancel that one too, so once
    // one has been outrun the next waits for the scroll to stop; the base
    // shows meanwhile.
    if (detailJob) { detailJob.task.cancel(); detailJob = null; outrun = true; }
    clearTimeout(quiet); quiet = 0;
    if (outrun) quiet = setTimeout(() => { quiet = 0; drawDetail().catch(report); }, SCROLL_QUIET_MS);
    else drawDetail().catch(report);
  }
  const viewChanged = () => { frame ||= requestAnimationFrame(onView); };

  // Follow the view only while a detail canvas is in use.
  function follow(on) {
    if (on === !!resized) return;
    if (on) {
      scroller.addEventListener("scroll", viewChanged, { passive: true });
      resized = new ResizeObserver(viewChanged);
      resized.observe(scroller);
    } else {
      scroller.removeEventListener("scroll", viewChanged);
      resized.disconnect(); resized = null;
      cancelAnimationFrame(frame); frame = 0;
    }
  }

  // Everything `want` needs: the part in view first, then the whole page.
  async function draw() {
    const target = want;
    const plan = basePlan(target.width, target.height, dpr());
    follow(plan.detail);
    if (plan.detail) await drawDetail();
    if (want !== target) return;
    await drawBase(target, plan);
    // Zoomed back out of detail: the old one stays, stretched, until the base that replaces it is in.
    if (want === target && !plan.detail) dropDetail();
  }

  function halt() {
    clearTimeout(settle); clearTimeout(quiet); settle = quiet = 0;
    outrun = false;
    baseTask?.cancel(); baseTask = null;
    detailJob?.task.cancel(); detailJob = null;
  }

  function show(page, { scale, annotationMode, pinned = false } = {}) {
    if (!page) {
      halt();
      want = shown = null;
      follow(false);
      base.clear();
      dropDetail();
      shared.holders.delete(holder);
      return;
    }
    if (want && want.page === page && want.scale === scale && want.annotationMode === annotationMode) { want.pinned = pinned; return; }
    halt();
    const { width, height } = page.getViewport({ scale });
    want = { page, scale, annotationMode, pinned, width, height };
    if (shown?.page === page) settle = setTimeout(() => { settle = 0; draw().catch(report); }, ZOOM_SETTLE_MS);
    else draw().catch(report);
  }

  return { show, dispose: () => show(null) };
}
