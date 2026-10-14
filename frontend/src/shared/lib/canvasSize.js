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
