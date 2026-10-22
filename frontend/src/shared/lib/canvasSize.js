// Bound both area and edge length: a large CSS page must never force a huge
// bitmap on iPad. CSS/SVG geometry stays at the requested zoom; only raster
// resolution is reduced. Allow ratios below 1 for oversized PDF page formats.
//
// WebKit refuses a canvas past 16 Mi pixels and fails allocations past a total
// of them. Every iPad browser is WebKit, and an iPad may report a Mac user
// agent, so there (and on any touch-first device) a canvas stays at 8 Mi and
// 4096 per edge. Desktop Chromium and Firefox take far more, and need it: at
// DPR 2 a 1920 × 1030 PDF viewer alone is 7.9 Mi, so under 8 Mi the detail
// canvas reached only a few pixels past it and every wheel notch flashed the
// soft base (docs/research/pdf-scroll-flash.md). There the limits are WebKit's
// own per-canvas maximum and twice the edge.
export function canvasLimits(win = typeof window === "undefined" ? null : window) {
  const roomy = !!win && !("GestureEvent" in win) && !win.matchMedia?.("(pointer: coarse)").matches;
  return roomy ? { pixels: 16 * 1024 * 1024, edge: 8192 } : { pixels: 8 * 1024 * 1024, edge: 4096 };
}
const LIMITS = canvasLimits();
export const CANVAS_MAX_PIXELS = LIMITS.pixels;
export const CANVAS_MAX_EDGE = LIMITS.edge;
// Backing pixels per CSS pixel a width × height box can have: the ratio asked
// for, or less where a limit bites. 0 for a box that is not a box.
export function canvasRatio(width, height, ratio = 1) {
  if (!(width > 0 && height > 0 && Number.isFinite(width) && Number.isFinite(height))) return 0;
  return Math.min(Number.isFinite(ratio) && ratio > 0 ? ratio : 1,
    CANVAS_MAX_EDGE / width, CANVAS_MAX_EDGE / height,
    Math.sqrt(CANVAS_MAX_PIXELS / width / height));
}
export function canvasSize(width, height, ratio = 1) {
  const r = canvasRatio(width, height, ratio);
  if (!r) return { width: 1, height: 1 };
  return { width: Math.max(1, Math.floor(width * r)), height: Math.max(1, Math.floor(height * r)) };
}

// A canvas laid over part of a box (the ink layer's live and wet canvases,
// ink/InkLayer.jsx): where `rect`, the box's screen rect ({left, top, right,
// bottom}), meets `view`, the screen rect it is seen through, in css px from
// the box's top-left and out to whole pixels — {x, y, width, height}, or
// null when none of the box shows. Over a page magnified past its viewer
// (the zoom window's at 4×, a page at 400 %) such a canvas holds what shows
// at the device's ratio, where one over the whole page would be held far
// below it by the caps above.
export function canvasWindow(rect, view) {
  const left = Math.max(rect.left, view.left), top = Math.max(rect.top, view.top);
  const right = Math.min(rect.right, view.right), bottom = Math.min(rect.bottom, view.bottom);
  if (!(right > left && bottom > top)) return null;
  const x = Math.floor(left - rect.left), y = Math.floor(top - rect.top);
  return { x, y, width: Math.ceil(right - rect.left) - x, height: Math.ceil(bottom - rect.top) - y };
}
// Whether window `a` holds window `b` (canvasWindow's).
export const windowHolds = (a, b) => b.x >= a.x && b.y >= a.y && b.x + b.width <= a.x + a.width && b.y + b.height <= a.y + a.height;
// The smallest window holding both.
export function windowUnion(a, b) {
  const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
  return { x, y, width: Math.max(a.x + a.width, b.x + b.width) - x, height: Math.max(a.y + a.height, b.y + b.height) - y };
}
// The transform (setTransform's six numbers) from page points, `k` css px
// each, to the pixels of a canvas whose backing store is `size` ({width,
// height}) laid on window `win` of the box.
export function windowMatrix(size, win, k) {
  const sx = size.width / win.width, sy = size.height / win.height;
  return [sx * k, 0, 0, sy * k, -win.x * sx, -win.y * sy];
}
