// One zoom policy for every viewer and every entry point (the toolbar buttons
// in App, Ctrl+scroll and pinch in the PDF and notebook viewers) — a limit
// change must not leave any two of them out of agreement.
export const ZOOM_MIN = 0.2, ZOOM_MAX = 8;
export const clampZoom = (s) => Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, s));
