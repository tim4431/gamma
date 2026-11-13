// The arithmetic of a region drawn on a picture (docs/dev/documents.md
// "Viewers", docs/dev/api.md "The highlight shape"): a drag's rectangle in
// the box's pixels, its place as an `image_position` in the picture's own
// pixels, and the fractions a reader draws it by. Pure, so node tests it
// (tests/regionGeometry.test.mjs); shared/ui/regionDrag.js and the image
// viewer (documents/viewers/FileViews.jsx) read it.

// A drag under this many screen px either way is a click, not a region.
export const MIN_DRAG_PX = 6;

const clamp = (v, max) => Math.max(0, Math.min(max, v));

// The rectangle a drag from (sx, sy) to (cx, cy) draws over a box (a
// DOMRect: left, top, width, height), in the box's px, clamped to it.
export function dragRect(sx, sy, cx, cy, box) {
  return {
    x1: clamp(Math.min(sx, cx) - box.left, box.width),
    y1: clamp(Math.min(sy, cy) - box.top, box.height),
    x2: clamp(Math.max(sx, cx) - box.left, box.width),
    y2: clamp(Math.max(sy, cy) - box.top, box.height),
  };
}

// A rectangle too small either way to be a drawn region.
export const isClick = (rect, minPx = MIN_DRAG_PX) => rect.x2 - rect.x1 < minPx || rect.y2 - rect.y1 < minPx;

// The frame a picture's regions are measured in: its own pixels as the
// browser reports them once loaded (EXIF orientation applied), else the box
// it is drawn in at scale 1 (an SVG with no intrinsic size, which the
// browser reports as 0 × 0 or its 300 × 150 default while nothing sets its
// size). `natural` is {width, height}; `box` the drawn size at scale 1.
export function imageFrame(natural, box) {
  const w = Math.round(natural?.width || 0), h = Math.round(natural?.height || 0);
  if (w > 0 && h > 0) return { width: w, height: h };
  const bw = Math.round(box?.width || 0), bh = Math.round(box?.height || 0);
  return bw > 0 && bh > 0 ? { width: bw, height: bh } : null;
}

// The stored place of a region: `rect` in the box's px (dragRect's, at the
// box's drawn size `box` {width, height}) mapped into the picture's frame
// (imageFrame's {width, height}), whole pixels, clamped to the frame, one
// rect equal to its bounding box, and `hash`, the stored file the region was
// drawn on. Null for a box or a frame of no size, or a rect that comes to
// nothing.
export function toImagePosition(rect, box, frame, hash) {
  if (!rect || !(box?.width > 0) || !(box?.height > 0) || !(frame?.width > 0) || !(frame?.height > 0)) return null;
  const kx = frame.width / box.width, ky = frame.height / box.height;
  const x1 = clamp(Math.round(rect.x1 * kx), frame.width), x2 = clamp(Math.round(rect.x2 * kx), frame.width);
  const y1 = clamp(Math.round(rect.y1 * ky), frame.height), y2 = clamp(Math.round(rect.y2 * ky), frame.height);
  if (!(x2 > x1) || !(y2 > y1)) return null;
  const r = { x1, y1, x2, y2 };
  return { width: frame.width, height: frame.height, boundingRect: r, rects: [{ ...r }], hash: String(hash || "") };
}

// Where a region stands on its picture, as fractions of the frame
// {left, top, width, height} in 0..1, clamped: what a reader draws it by,
// whatever size the picture shows at. Null for a malformed position.
export function regionFractions(pos) {
  const r = pos?.boundingRect;
  const w = Number(pos?.width), h = Number(pos?.height);
  if (!r || !(w > 0) || !(h > 0)) return null;
  const x1 = clamp(Number(r.x1) / w, 1), x2 = clamp(Number(r.x2) / w, 1);
  const y1 = clamp(Number(r.y1) / h, 1), y2 = clamp(Number(r.y2) / h, 1);
  if (![x1, x2, y1, y2].every(Number.isFinite) || !(x2 > x1) || !(y2 > y1)) return null;
  return { left: x1, top: y1, width: x2 - x1, height: y2 - y1 };
}
