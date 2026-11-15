// One-finger scrolling along the pages' run (pdf/pageLayout.js): down the
// pages, or across them when they run side by side. Native touch scrolling
// and inertia stay entirely browser-owned. A small drift the other way (a
// zoomed page sliding sideways under a downward swipe, or up and down under
// an across one) is corrected ONCE after scrolling settles, never during a
// fling.
const MIN_TRAVEL = 8;
const ALONG_RATIO = Math.tan(Math.PI / 6);
const IDLE_MS = 250; // fallback for older Safari without scrollend

// `across`: the pages run across, so a swipe along them is sideways and the
// drift corrected is vertical.
export function installScrollSnap(el, { across = false } = {}) {
  // the offset a drift moves, and the scroll range it moves in
  const drift = across ? "scrollTop" : "scrollLeft";
  const room = () => (across ? el.scrollHeight - el.clientHeight : el.scrollWidth - el.clientWidth);
  let gesture = null, target = null, idle = 0;
  const hasScrollEnd = "onscrollend" in el;
  const release = () => { clearTimeout(idle); idle = 0; gesture = null; target = null; };
  const settle = () => {
    if (gesture || target === null) return;
    const to = Math.max(0, Math.min(target, room()));
    release(); // the correction's scroll events must not schedule another snap
    if (Math.abs(el[drift] - to) > 1.5) {
      el.scrollTo({ [across ? "top" : "left"]: to, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    }
  };
  const armIdle = () => {
    clearTimeout(idle);
    if (!hasScrollEnd && !gesture && target !== null) idle = setTimeout(settle, IDLE_MS);
  };
  const start = (e) => {
    release();
    if (e.touches.length !== 1 || room() <= 1) return;
    const t = e.touches[0];
    gesture = { x: t.clientX, y: t.clientY, at: el[drift], decided: false };
  };
  const move = (e) => {
    if (!gesture) return;
    if (e.touches.length !== 1) { release(); return; }
    const dx = Math.abs(e.touches[0].clientX - gesture.x), dy = Math.abs(e.touches[0].clientY - gesture.y);
    // the travel along the run, and the travel against it
    const along = across ? dx : dy, against = across ? dy : dx;
    if (!gesture.decided) {
      if (Math.hypot(dx, dy) < MIN_TRAVEL) return;
      if (against > along * ALONG_RATIO) { release(); return; }
      gesture.decided = true;
      target = gesture.at;
    } else if (against > Math.max(24, along * 0.8)) {
      release(); // user turned away from the run deliberately: do not pull them back
    }
  };
  const end = (e) => {
    if (e.touches.length) { release(); return; }
    gesture = null;
    armIdle();
  };
  const pointer = (e) => {
    if (e.pointerType !== "touch" || !el.contains(e.target)) release();
  };
  const bindings = [["touchstart", start], ["touchmove", move], ["touchend", end],
    ["touchcancel", release], ["scroll", armIdle], ["scrollend", settle], ["wheel", release]];
  for (const [name, fn] of bindings) el.addEventListener(name, fn, { passive: true });
  document.addEventListener("pointerdown", pointer, true);
  document.addEventListener("keydown", release, true);
  window.addEventListener("blur", release);
  window.addEventListener("resize", release);
  return () => {
    release();
    for (const [name, fn] of bindings) el.removeEventListener(name, fn);
    document.removeEventListener("pointerdown", pointer, true);
    document.removeEventListener("keydown", release, true);
    window.removeEventListener("blur", release);
    window.removeEventListener("resize", release);
  };
}
