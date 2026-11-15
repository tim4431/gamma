// Smooth scrolling for a notched mouse wheel (Settings → Reading › PDFs).
//
// A wheel with notches scrolls in jumps, one notch at a time (Chromium moves
// 100 px per three lines of the system's wheel setting), and a browser
// animates them only when its own smooth scrolling is on. With this on, the
// scroller glides toward where the notches point instead: every frame it
// closes a fixed share of what is left, so a notch that arrives on the way
// only moves the goal and the motion carries on without a break. A touchpad
// or a precise wheel already sends many small deltas and stays native, as do
// Shift (sideways), Ctrl / ⌘ (zoom, shared/lib/viewerZoom.js) and a wheel
// over an inner element that can scroll itself. Anything else that moves the
// scroller meanwhile (a jump, a zoom holding its anchor, the scrollbar, a
// key) ends the motion. Over a viewer whose pages run across (`across`,
// pdf/pageLayout.js) with nothing to scroll down, a notch glides the pages
// across instead; installWheelAcross below moves them for any other wheel.
//
// The frame clock, not the wheel's, times the motion. Chromium hands a wheel
// event to the page inside the frame it belongs to, just before that frame's
// animation callbacks, and stamps the callbacks with the frame's start, which
// is before the event. A motion measured from the event's own time therefore
// found no time passed in its first frame and wrote the position it started
// from: every notch cost one frame of standing still, and a fast spin, one
// notch to a frame, stood still as long as it lasted. Here a notch never
// resets anything; the first frame after rest is taken as a frame long.
//
// installSmoothWheel(el, { across }) and installWheelAcross(el) return their
// cleanup.

// A notch at rest closes 95% of its distance in about SMOOTH_MS.
export const SMOOTH_MS = 150;
const TAU_MS = SMOOTH_MS / 3; // the time constant of the approach
const FRAME_MS = 16; // the first frame after rest counts as this long
export const LINE_PX = 40; // a line of deltaMode 1, as Firefox reports a notch
const DRIFT_PX = 2; // how far scrollTop may read back from what was written
const DONE_PX = 1; // this close, the goal is written exactly and the motion ends

// Whether a legacy wheelDeltaY counts whole notches: 120 each, divided by
// `ratio` and truncated toward zero. Chromium divides by devicePixelRatio,
// so a notch reads 109 at 110% zoom, 96 on a 125% display; WebKit does not
// divide (ratio 1). A touchpad or a precise wheel moves by fractions of one.
export function wholeNotches(legacy, ratio) {
  if (typeof legacy !== "number" || !legacy) return false;
  const n = Math.round(legacy * ratio / 120);
  return n !== 0 && Math.trunc(n * 120 / ratio) === legacy;
}

// The vertical distance a wheel event asks for when it comes from a notch,
// else 0: lines and pages always do; in pixel mode the legacy wheelDeltaY
// tells, read at the page's devicePixelRatio `dpr` (Chromium) or as is.
export function notchDelta(e, pageHeight, dpr = 1) {
  if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.deltaX || !e.deltaY) return 0;
  if (e.deltaMode === 1) return e.deltaY * LINE_PX;
  if (e.deltaMode === 2) return e.deltaY * pageHeight;
  return wholeNotches(e.wheelDeltaY, dpr) || wholeNotches(e.wheelDeltaY, 1) ? e.deltaY : 0;
}

// The share of the remaining distance a frame of `dt` ms closes.
export const approach = (dt) => 1 - Math.exp(-Math.max(0, dt) / TAU_MS);

// Whether an element between `node` and the scroller can scroll by `dy`
// itself: the wheel is its (also the code view's comment column,
// TextOverlay.jsx, whose wheel scrolls the text beside it).
export function innerScrolls(node, el, dy) {
  for (let n = node; n && n !== el && n.nodeType === 1; n = n.parentElement) {
    const { overflowY } = getComputedStyle(n);
    if ((overflowY === "auto" || overflowY === "scroll") && n.scrollHeight > n.clientHeight + 1
      && (dy < 0 ? n.scrollTop > 0 : n.scrollTop + n.clientHeight < n.scrollHeight - 1)) return true;
  }
  return false;
}

// Whether a scroller has anything to scroll down.
const scrollsDown = (el) => el.scrollHeight > el.clientHeight + 1;

export function installSmoothWheel(el, { across = false } = {}) {
  let to = null;  // where the motion is heading; null at rest
  let pos = 0;    // the exact position, which the offset rounds
  let wrote = 0;  // what the offset read back after the last write
  let last = 0;   // the previous frame's timestamp; 0 at rest
  let frame = 0;
  let side = "scrollTop"; // the offset the motion moves: scrollLeft across
  const stop = () => { cancelAnimationFrame(frame); frame = 0; to = null; last = 0; };
  const step = (now) => {
    frame = 0;
    if (Math.abs(el[side] - wrote) > DRIFT_PX) { stop(); return; }
    const left = to - pos;
    pos = Math.abs(left) <= DONE_PX ? to : pos + left * approach(last ? now - last : FRAME_MS);
    last = now;
    el[side] = pos;
    wrote = el[side];
    if (pos === to) stop();
    else frame = requestAnimationFrame(step);
  };
  const onWheel = (e) => {
    const sideways = across && !scrollsDown(el);
    const dy = notchDelta(e, sideways ? el.clientWidth : el.clientHeight, globalThis.devicePixelRatio || 1);
    if (!dy || innerScrolls(e.target, el, dy)) return;
    const next = sideways ? "scrollLeft" : "scrollTop";
    if (to !== null && next !== side) stop(); // a zoom between two notches gave the pages room down, or took it
    side = next;
    const here = to ?? el[side];
    const end = sideways ? el.scrollWidth - el.clientWidth : el.scrollHeight - el.clientHeight;
    const goal = Math.max(0, Math.min(end, here + dy));
    if (goal === here) return; // at an end: the browser's own overscroll
    e.preventDefault();
    if (to === null) pos = wrote = el[side];
    to = goal;
    if (!frame) frame = requestAnimationFrame(step);
  };
  el.addEventListener("wheel", onWheel, { passive: false });
  return () => { stop(); el.removeEventListener("wheel", onWheel); };
}

// The distance across a wheel event asks of pages that run across: its
// vertical delta in px (a notch's lines or pages converted, a touchpad's
// pixels as they are), or 0 for one that is sideways already or carries a
// modifier (Shift turns it sideways natively, Ctrl / ⌘ is the zoom).
export function acrossDelta(e, width) {
  if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || !e.deltaY || Math.abs(e.deltaX || 0) >= Math.abs(e.deltaY)) return 0;
  if (e.deltaMode === 1) return e.deltaY * LINE_PX;
  if (e.deltaMode === 2) return e.deltaY * width;
  return e.deltaY;
}

// A viewer whose pages run across, with nothing to scroll down (the pages
// fit its height): a wheel turned down or up moves the pages across, where
// it would otherwise move nothing. With something to scroll down (a zoom
// taller than the view) the wheel scrolls down as ever. Smooth wheel
// scrolling, installed before this, takes a notch itself.
export function installWheelAcross(el) {
  const onWheel = (e) => {
    if (e.defaultPrevented || scrollsDown(el)) return;
    const dx = acrossDelta(e, el.clientWidth);
    if (!dx || innerScrolls(e.target, el, dx)) return;
    const goal = Math.max(0, Math.min(el.scrollWidth - el.clientWidth, el.scrollLeft + dx));
    if (goal === el.scrollLeft) return; // at an end: nothing to move
    e.preventDefault();
    el.scrollLeft = goal;
  };
  el.addEventListener("wheel", onWheel, { passive: false });
  return () => el.removeEventListener("wheel", onWheel);
}
