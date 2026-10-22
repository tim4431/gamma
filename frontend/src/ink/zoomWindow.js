// The zoom window's rules (docs/dev/handwriting.md "Zoom window"), pure:
// App's `inkZoom` is the window, {page, x, y, w, h, mag, line, x0}: the PDF
// page's number; the window's top-left, width and height in page points;
// the magnification, css px per point in the panel (ZOOM_MAG_MIN to
// ZOOM_MAG_MAX); the line height in points; and where the line began, `x0`.
// `size` is the page's {width, height} in points and `panel` the panel's
// view {width, height} in css px, so w × mag is the view's width. Every
// answer is clamped to its page, and one that changes nothing is the window
// itself, so a state update with it renders nothing.
import { round2 } from "./ink.js";

export const ZOOM_MAGS = [2, 3, 4];
export const ZOOM_MAG = 3;
export const ZOOM_MAG_MIN = 2;
export const ZOOM_MAG_MAX = 4;
export const ZOOM_LINE = 24;
export const ZOOM_LINE_MIN = 8;
export const ZOOM_LINE_MAX = 96;
export const ZOOM_LINE_STEP = 2;
// The panel's height in css px (this browser's inkZoomHeight), grip and bar included.
export const ZOOM_HEIGHT = 200;
export const ZOOM_HEIGHT_MIN = 160;
export const ZOOM_HEIGHT_MAX = 320;
// What the panel's top border, grip and bar take of its height, css px
// (app.css .inkZoomPanel): the view a window opened before the panel has
// measured itself is assumed to have the rest (ink/ZoomWindow.jsx keeps w
// and h to the view it measures).
export const ZOOM_CHROME = 45;
// Auto-advance: a stroke whose box reaches past ADVANCE_AT of the window's
// width moves the window on by ADVANCE_BY of it.
export const ADVANCE_AT = 0.8;
export const ADVANCE_BY = 0.6;

const KEYS = ["page", "x", "y", "w", "h", "mag", "line", "x0"];
const same = (a, b) => KEYS.every((k) => a[k] === b[k]);

// The window inside its page: x and y (and x0 with them) between 0 and the
// page's size less the window's; a window wider or taller than its page
// keeps its top-left corner on it.
export function clampWindow(win, size) {
  const W = size?.width || 0, H = size?.height || 0;
  const inX = (v) => round2(Math.max(0, Math.min(W - win.w, v)));
  const next = { ...win, x: inX(win.x), y: round2(Math.max(0, Math.min(H - win.h, win.y))), x0: inX(win.x0 ?? win.x) };
  return same(win, next) ? win : next;
}
// `next` held to the page, or `win` itself when that is where it ends up.
const settle = (win, next, size) => {
  const out = clampWindow(next, size);
  return same(win, out) ? win : out;
};

// A window opened on `page`: its size from the panel at ZOOM_MAG, at the
// left of the part of the page on the screen (`visible`, in page points;
// the whole page without one), a twentieth of the page's width in, its
// middle at the visible part's; the line starts there.
export function openWindow(page, size, visible, panel) {
  const mag = ZOOM_MAG;
  const w = round2(Math.max(1, panel?.width || 0) / mag), h = round2(Math.max(1, panel?.height || 0) / mag);
  const v = visible || { x: 0, y: 0, width: size.width, height: size.height };
  const x = Math.max(0, v.x) + size.width / 20;
  const y = v.y + v.height / 2 - h / 2;
  return clampWindow({ page, x, y, w, h, mag, line: ZOOM_LINE, x0: x }, size);
}

// The window's w and h again when the panel's view changes size: the view's
// css px over the magnification, the top-left kept.
export function sized(win, panel, size) {
  if (!(panel?.width > 0 && panel?.height > 0)) return win;
  const w = round2(panel.width / win.mag), h = round2(panel.height / win.mag);
  return settle(win, { ...win, w, h }, size);
}

// One line down from where the line began (New line, Enter); at the
// page's bottom it stops: a window that cannot go down stays where it is.
export function newLine(win, size) {
  const next = settle(win, { ...win, x: win.x0, y: win.y + win.line }, size);
  return next.y > win.y ? next : win;
}

// One line down or up, x kept (Next line, Previous line).
export const nextLine = (win, size) => settle(win, { ...win, y: win.y + win.line }, size);
export const prevLine = (win, size) => settle(win, { ...win, y: win.y - win.line }, size);

// After a stroke written in the window, whose box ([x0, y0, x1, y1] in page
// points, ink.js strokeBounds) reached past ADVANCE_AT of its width: on
// along the line by ADVANCE_BY of it, or, when that would carry the window
// past the page's right edge, back to where the line began, one line down.
export function advance(win, box, size) {
  if (!box || !(box[2] > win.x + ADVANCE_AT * win.w)) return win;
  const x = win.x + ADVANCE_BY * win.w;
  if (x + win.w <= (size?.width || 0) + 0.01) return settle(win, { ...win, x }, size);
  return newLine(win, size);
}

// A move by hand: the window's top-left at {x, y} (the page's drag band),
// or its middle at `point` on `page` (a tap on a page, which may be another
// one: `size` is that page's), the line starting where it lands.
export const moveWindow = (win, { x, y }, size) => settle(win, { ...win, x, y, x0: x }, size);
export function moveTo(win, page, point, size) {
  const x = point.x - win.w / 2;
  return settle(win, { ...win, page, x, y: point.y - win.h / 2, x0: x }, size);
}

// Another magnification, held to ZOOM_MAG_MIN … ZOOM_MAG_MAX: w and h from
// the panel's view (the window's own w × mag without one), the left edge
// and the vertical middle kept, so the line being written stays in view.
export function setMag(win, mag, panel, size) {
  const m = round2(Math.max(ZOOM_MAG_MIN, Math.min(ZOOM_MAG_MAX, Number(mag) || ZOOM_MAG)));
  if (m === win.mag) return win;
  const view = panel?.width > 0 && panel?.height > 0 ? panel : { width: win.w * win.mag, height: win.h * win.mag };
  const w = round2(view.width / m), h = round2(view.height / m);
  return settle(win, { ...win, mag: m, w, h, y: win.y + win.h / 2 - h / 2 }, size);
}

// The magnification that makes the window `width` points wide in the same
// panel (the page's resize corner), before setMag holds it in range.
export const magForWidth = (win, width) => win.w * win.mag / Math.max(1, width);

// The line height, ZOOM_LINE_MIN … ZOOM_LINE_MAX in steps of ZOOM_LINE_STEP.
export function setLine(win, line) {
  const n = Math.max(ZOOM_LINE_MIN, Math.min(ZOOM_LINE_MAX, Math.round((Number(line) || ZOOM_LINE) / ZOOM_LINE_STEP) * ZOOM_LINE_STEP));
  return n === win.line ? win : { ...win, line: n };
}

// How far a scroller must scroll ({dx, dy} in css px) to bring `rect` into
// `view` (both screen rects), `pad` px clear of its edges where there is
// room: nothing when it is in view, its top-left first when it cannot fit.
export function revealDelta(rect, view, pad = 16) {
  const axis = (lo, hi, vlo, vhi) => {
    if (lo >= vlo && hi <= vhi) return 0;
    if (hi - lo > vhi - vlo - 2 * pad) return lo - (vlo + pad);
    return lo < vlo ? lo - (vlo + pad) : hi - (vhi - pad);
  };
  return { dx: axis(rect.left, rect.right, view.left, view.right), dy: axis(rect.top, rect.bottom, view.top, view.bottom) };
}
