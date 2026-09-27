// Wheel-to-horizontal-pan for a sideways strip (the home library's card
// strips, the tab strip): a plain vertical mouse wheel scrolls it sideways.
// A native non-passive listener, because React's synthetic onWheel can't
// preventDefault; touch swipes pan natively via overflow-x. Returns a
// callback ref (not a plain one) so the listener follows the element
// through conditional mounts.
import { useCallback, useRef } from "react";

export function useWheelPan() {
  const cleanupRef = useRef(null);
  return useCallback((el) => {
    if (cleanupRef.current) { cleanupRef.current(); cleanupRef.current = null; }
    if (!el) return;
    function onWheel(e) {
      // Real horizontal input (trackpads, tilt wheels) already works; pinch
      // gestures (ctrlKey) belong to the browser zoom.
      if (e.ctrlKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return;
      if (el.scrollWidth <= el.clientWidth) return; // nothing to pan → page scrolls
      el.scrollLeft += e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY; // LINE mode (Firefox) → ~px
      e.preventDefault();
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    cleanupRef.current = () => el.removeEventListener("wheel", onWheel);
  }, []);
}
