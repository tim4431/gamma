// The scale a viewer's pages are drawn at, for the code inside a page that
// needs it as a number. The pages themselves size with the zoom in CSS
// (shared/lib/viewerZoom.js SCALE_VAR) and take no scale prop, so a zoom does
// not render them; what has to redo work for a zoom reads it here.
//   get()   the scale the boxes are laid out at now: the raster's (it keeps
//           its own settle, pdf/pageRaster.js).
//   rest()  that scale once the zoom has stopped for ZOOM_SETTLE_MS: the text
//           layer is re-measured, and a translation laid out again, at it.
//   set(s)  the viewer's committed scale.
//   subscribe(fn) → off  fn runs when either value moves.
//   dispose()  drops a pending rest; set() still works after it, so
//           React.StrictMode's second run of the effects is harmless.
import { ZOOM_SETTLE_MS } from "./pageRaster.js";

export function createPageScale(initial) {
  let live = initial, settled = initial, timer = 0;
  const subs = new Set();
  const notify = () => { for (const fn of [...subs]) fn(); };
  return {
    get: () => live,
    rest: () => settled,
    set(s) {
      if (s === live) return;
      live = s;
      clearTimeout(timer);
      timer = setTimeout(() => {
        timer = 0;
        if (settled === live) return;
        settled = live;
        notify();
      }, ZOOM_SETTLE_MS);
      notify();
    },
    subscribe(fn) {
      subs.add(fn);
      return () => { subs.delete(fn); };
    },
    dispose() { clearTimeout(timer); timer = 0; },
  };
}
