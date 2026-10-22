// The zoom window (docs/dev/handwriting.md "Client", ink/zoomWindow.js
// for its rules, ink/ZoomWindow.jsx for its panel): the window on one PDF
// page, session state that goes with the strip, the page, the PDF, the
// notebook view and a phone's layout; opening it on the page in view,
// its New line, and the panel's and the layers' changes to it. App
// composes it after the ruler.
import { useCallback, useEffect, useRef, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { visiblePart } from "./useInkSurfaces.js";
import { ZOOM_CHROME, moveTo, moveWindow as moveZoomWindow, newLine, openWindow, setMag } from "./zoomWindow.js";   // the zoom window's rules (moveWindow as moveZoomWindow, the name handleInkZoom calls it by)

// focusedBlockId: the open page; another one closes the window.
// readOnly: no window opens. isPhone: a phone's layout, where it never
// opens. pdfHidden, notebook: the viewer shows no PDF page, or the
// notebook view's sheets; either closes it. inkZoomHeight: this
// browser's height of the panel in css px. viewerWrapRef: the viewer's
// wrapper, read when the window opens. pasteTarget(): the surface in
// view (useInkSurfaces.js). setStatus: the status pill.
export function useInkZoom({ focusedBlockId, readOnly, isPhone, pdfHidden, notebook, inkZoomHeight,
  viewerWrapRef, pasteTarget, setStatus }) {
  // The zoom window (ink/zoomWindow.js, docs/dev/handwriting.md "Zoom
  // window"): {page, x, y, w, h, mag, line, x0} in the PDF page's points
  // while it is open — session state, gone with the strip and with the page
  // — and the size of its page in points.
  const [inkZoom, setInkZoom] = useState(null);
  const inkZoomSizeRef = useRef(null);
  // The strip closes (App's closeInkStrip): the window closes.
  const clearInkZoom = useCallback(() => setInkZoom(null), []);
  // The zoom window is a PDF page's, on a wide screen: another page, the
  // PDF hidden, the notebook view or a phone's layout close it.
  useEffect(() => { setInkZoom(null); }, [focusedBlockId, pdfHidden, notebook, isPhone]);
  // The zoom window (More → Zoom window, Z): opened on the PDF page in view,
  // its middle at the middle of what shows of the page above where the panel
  // will stand, or closed. Never on a phone; a sheet of paper says it cannot.
  function toggleInkZoom() {
    if (isPhone || readOnly) return;
    if (inkZoom) { setInkZoom(null); return; }
    const target = pasteTarget();
    if (!target || typeof target.page !== "number") { setStatus(t("The zoom window works on PDF pages.")); return; }
    const height = inkZoomHeight;
    const scroller = viewerWrapRef.current?.querySelector(".pdfViewer");
    const wrap = scroller?.querySelector(`.pdfPageWrap[data-page="${target.page}"]`);
    const k = wrap ? wrap.getBoundingClientRect().width / target.size.width : 0;
    const seen = visiblePart(target.page, target.size);
    const visible = seen && k > 0 ? { ...seen, height: Math.max(1, seen.height - height / k) } : seen;
    inkZoomSizeRef.current = target.size;
    setInkZoom(openWindow(target.page, target.size, visible, { width: scroller?.clientWidth || 600, height: height - ZOOM_CHROME }));
  }
  // Enter while the window is open: its New line.
  function inkZoomNewLine() {
    const size = inkZoomSizeRef.current;
    if (size) setInkZoom((win) => (win ? newLine(win, size) : win));
  }
  // The panel's own changes (its bar, the auto-advance, its view's size):
  // `update(window) → next`, null closing it.
  const changeInkZoom = useCallback((update) => setInkZoom((win) => (win ? update(win) : win)), []);
  // The page's layers: the window's box dragged ({x, y}, its line starting
  // there) or resized ({mag}), or a blank tap on a PDF page ({at, size}: the
  // window centred there, on that page).
  function handleInkZoom(page, patch) {
    if (typeof page !== "number" || !patch) return;
    if (patch.at && patch.size) inkZoomSizeRef.current = patch.size;
    const size = inkZoomSizeRef.current;
    setInkZoom((win) => {
      if (!win || !size) return win;
      if (patch.at) return moveTo(win, page, patch.at, size);
      if (page !== win.page) return win;
      return "mag" in patch ? setMag(win, patch.mag, null, size) : moveZoomWindow(win, patch, size);
    });
  }
  return { inkZoom, toggleInkZoom, inkZoomNewLine, changeInkZoom, handleInkZoom, clearInkZoom };
}
