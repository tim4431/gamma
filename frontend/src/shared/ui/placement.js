// Where a floating surface goes (a menu, a popup, a tip), from its own
// measured size — one story for all of them (docs/dev/ui-design.md, "Menus
// and popups open in place"). The size is only known once the surface is
// laid out, so it is placed in a layout effect, before the browser paints
// it; until then the caller keeps it hidden. When its size changes later (a
// row of options opens, a translation arrives), or the window's does, it is
// placed again in that same frame: the update goes through flushSync, since
// a state update from a ResizeObserver, a scroll listener or an animation
// frame otherwise renders only after the browser has painted, and that
// frame shows the surface where it stood — over its button, or on the side
// it is about to leave.
import { useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";

// Gap a surface keeps to the edge of the window (or of `bounds`).
export const VIEWPORT_PAD = 8;

// The size a surface lays out at, without the scale it arrives with: a
// menu grows in from var(--scale-in) (motion.css), and its rect is that
// much smaller until it has. Place a surface by this, not by its rect.
// Only its own scale is taken out, not an ancestor's.
export function surfaceSize(el) {
  const { width, height } = el.getBoundingClientRect();
  const scale = parseFloat(getComputedStyle(el).scale); // "none" at rest
  return scale > 0 && scale !== 1 ? { width: width / scale, height: height / scale } : { width, height };
}

// The transform-origin that makes a surface placed at {left, top} grow
// from the point (x, y) it opens from, and shrink back toward it
// (motion.css): the cursor, or the corner at its button, also when the
// surface took the other side of it or was kept inside the window.
export const growFrom = (x, y, { left, top }) => `${x - left}px ${y - top}px`;

// The top-left corner for a surface of `size` at (x, y): x is its left
// edge, or its right one with `anchorRight`; y its top edge, or its bottom
// one with `anchorBottom` (it opens upward). `flipY` is the other side of
// what it opens from — the top edge for a surface that opens downward, the
// bottom for one that opens upward — taken when its own side has no room
// and that one has. Then it is kept inside the window, and vertically
// inside `bounds` ({top, bottom}; the window's by default).
export function placeFloating({ width, height }, { x, y, anchorRight = false, anchorBottom = false, flipY, bounds },
  view = { width: window.innerWidth, height: window.innerHeight }) {
  const pad = VIEWPORT_PAD;
  const lo = (bounds?.top ?? 0) + pad, hi = (bounds?.bottom ?? view.height) - pad;
  let left = anchorRight ? x - width : x;
  let top = anchorBottom ? y - height : y;
  if (flipY != null) {
    if (anchorBottom && top < lo && flipY + height <= hi) top = flipY;
    else if (!anchorBottom && top + height > hi && flipY - height >= lo) top = flipY - height;
  }
  if (left + width > view.width - pad) left = view.width - width - pad;
  if (left < pad) left = pad;
  if (top + height > hi) top = hi - height;
  if (top < lo) top = lo;
  return { left, top };
}

// Gap between a button and the menu it opens.
const MENU_GAP = 4;

// Where a menu that opens from a button goes, as ContextMenu's anchor, from
// the button's rect `r`: its left edge on the button's, and with `side`
// "below" 4 px under the button, with "above" its bottom edge 4 px over it.
// Spread the result on the ContextMenu, which keeps the menu inside the
// window (placeFloating).
export function menuAnchor(r, { side = "below" } = {}) {
  return {
    x: r.left,
    y: side === "above" ? r.top - MENU_GAP : r.bottom + MENU_GAP,
    anchorBottom: side === "above",
  };
}

// The {left, top} of the element `ref` holds, from place(element) — which
// measures it (surfaceSize) — again whenever `deps` change, its size or the
// window's does, and with `follow` whenever anything scrolls (an anchor
// that moves with the page). What else place returns comes along (an
// `origin` from growFrom). Null until it is first placed: render the
// element hidden at a provisional spot until then, so no frame of it is
// ever seen there.
export function useFloatingPosition(ref, place, deps, { follow = false } = {}) {
  const [pos, setPos] = useState(null);
  const placeRef = useRef(place);
  placeRef.current = place;
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const apply = (now) => {
      const placed = placeRef.current(el); // {left, top}, and what else the caller keeps (an origin)
      const same = (old) => old && Object.keys(placed).every((k) => old[k] === placed[k]);
      const set = () => setPos((old) => (same(old) ? old : placed));
      if (now) flushSync(set); else set();
    };
    apply(false);
    const again = () => apply(true);
    const observer = new ResizeObserver(again);
    observer.observe(el);
    const viewport = follow ? window.visualViewport : null;
    window.addEventListener("resize", again);
    if (follow) window.addEventListener("scroll", again, true);
    viewport?.addEventListener("resize", again);
    viewport?.addEventListener("scroll", again);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", again);
      window.removeEventListener("scroll", again, true);
      viewport?.removeEventListener("resize", again);
      viewport?.removeEventListener("scroll", again);
    };
  }, [ref, follow, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps
  return pos;
}
