// Smooth scrolling for a notched mouse wheel (Settings → Reading › PDFs).
//
// A wheel with notches scrolls in jumps, one notch at a time (Chromium moves
// 100 px per three lines of the system's wheel setting), and a browser
// animates them only when its own smooth scrolling is on. With this on, each
// notch eases over SMOOTH_MS instead, and a notch that arrives on the way
// extends the motion from where it is. A touchpad or a precise wheel already
// sends many small deltas and stays native, as do Shift (sideways), Ctrl / ⌘
// (zoom, shared/lib/viewerZoom.js) and a wheel over an inner element that
// can scroll itself. Anything else that moves the scroller meanwhile (a
// jump, a zoom holding its anchor, the scrollbar, a key) ends the motion.
//
// installSmoothWheel(el) returns its cleanup.

export const SMOOTH_MS = 160;
const LINE_PX = 40; // a line of deltaMode 1, as Firefox reports a notch
const DRIFT_PX = 2; // how far scrollTop may read back from what was written

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

export const easeOut = (k) => 1 - (1 - k) ** 3;

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
  let from = 0, to = null, start = 0, frame = 0, wrote = 0;
  const stop = () => { cancelAnimationFrame(frame); frame = 0; to = null; };
  const step = (now) => {
    frame = 0;
    if (Math.abs(el.scrollTop - wrote) > DRIFT_PX) { stop(); return; }
    // A frame's timestamp is when the frame began, which can precede the wheel event.
    const k = Math.max(0, Math.min(1, (now - start) / SMOOTH_MS));
    el.scrollTop = from + (to - from) * easeOut(k);
    wrote = el.scrollTop;
    if (k < 1) frame = requestAnimationFrame(step);
    else stop();
  };
  const onWheel = (e) => {
    const dy = notchDelta(e, el.clientHeight);
    if (!dy || innerScrolls(e.target, el, dy)) return;
    const here = to ?? el.scrollTop;
    const goal = Math.max(0, Math.min(el.scrollHeight - el.clientHeight, here + dy));
    if (goal === here) return; // at an end: the browser's own overscroll
    e.preventDefault();
    from = wrote = el.scrollTop;
    to = goal;
    start = performance.now();
    if (!frame) frame = requestAnimationFrame(step);
  };
  el.addEventListener("wheel", onWheel, { passive: false });
  return () => { stop(); el.removeEventListener("wheel", onWheel); };
}
