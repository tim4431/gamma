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
// key) ends the motion.
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
// installSmoothWheel(el) returns its cleanup.

// A notch at rest closes 95% of its distance in about SMOOTH_MS.
export const SMOOTH_MS = 150;
const TAU_MS = SMOOTH_MS / 3; // the time constant of the approach
const FRAME_MS = 16; // the first frame after rest counts as this long
const LINE_PX = 40; // a line of deltaMode 1, as Firefox reports a notch
const DRIFT_PX = 2; // how far scrollTop may read back from what was written
const DONE_PX = 1; // this close, the goal is written exactly and the motion ends

// The vertical distance a wheel event asks for when it comes from a notch,
// else 0. In pixel mode a notch still reports the legacy wheelDeltaY in
// steps of 120; a touchpad's deltas are arbitrary.
export function notchDelta(e, pageHeight) {
  if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.deltaX || !e.deltaY) return 0;
  if (e.deltaMode === 1) return e.deltaY * LINE_PX;
  if (e.deltaMode === 2) return e.deltaY * pageHeight;
  const legacy = e.wheelDeltaY;
  return typeof legacy === "number" && legacy !== 0 && legacy % 120 === 0 ? e.deltaY : 0;
}

// The share of the remaining distance a frame of `dt` ms closes.
export const approach = (dt) => 1 - Math.exp(-Math.max(0, dt) / TAU_MS);

// Whether an element between `node` and the scroller can scroll by `dy`
// itself: the wheel is its.
function innerScrolls(node, el, dy) {
  for (let n = node; n && n !== el && n.nodeType === 1; n = n.parentElement) {
    const { overflowY } = getComputedStyle(n);
    if ((overflowY === "auto" || overflowY === "scroll") && n.scrollHeight > n.clientHeight + 1
      && (dy < 0 ? n.scrollTop > 0 : n.scrollTop + n.clientHeight < n.scrollHeight - 1)) return true;
  }
  return false;
}

export function installSmoothWheel(el) {
  let to = null;  // where the motion is heading; null at rest
  let pos = 0;    // the exact position, which scrollTop rounds
  let wrote = 0;  // what scrollTop read back after the last write
  let last = 0;   // the previous frame's timestamp; 0 at rest
  let frame = 0;
  const stop = () => { cancelAnimationFrame(frame); frame = 0; to = null; last = 0; };
  const step = (now) => {
    frame = 0;
    if (Math.abs(el.scrollTop - wrote) > DRIFT_PX) { stop(); return; }
    const left = to - pos;
    pos = Math.abs(left) <= DONE_PX ? to : pos + left * approach(last ? now - last : FRAME_MS);
    last = now;
    el.scrollTop = pos;
    wrote = el.scrollTop;
    if (pos === to) stop();
    else frame = requestAnimationFrame(step);
  };
  const onWheel = (e) => {
    const dy = notchDelta(e, el.clientHeight);
    if (!dy || innerScrolls(e.target, el, dy)) return;
    const here = to ?? el.scrollTop;
    const goal = Math.max(0, Math.min(el.scrollHeight - el.clientHeight, here + dy));
    if (goal === here) return; // at an end: the browser's own overscroll
    e.preventDefault();
    if (to === null) pos = wrote = el.scrollTop;
    to = goal;
    if (!frame) frame = requestAnimationFrame(step);
  };
  el.addEventListener("wheel", onWheel, { passive: false });
  return () => { stop(); el.removeEventListener("wheel", onWheel); };
}
