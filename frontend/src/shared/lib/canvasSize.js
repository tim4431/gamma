// Bound both area and edge length: a large CSS page must never force a huge
// bitmap on iPad. CSS/SVG geometry stays at the requested zoom; only raster
// resolution is reduced. Allow ratios below 1 for oversized PDF page formats.
export const CANVAS_MAX_PIXELS = 8 * 1024 * 1024;
export const CANVAS_MAX_EDGE = 4096;
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
