// Where the PDF selection popup opens (PdfViewer's TipFrame): what it hangs
// from, as {top, bottom, left, gap} in viewport pixels. It opens `gap`
// below `bottom` with its left edge at `left`, or ends `gap` above `top`
// when there is no room below (placeFloating keeps it inside the window).

// How far from the selection the mouse may be released for the popup to
// open at the pointer (px); further off (a release outside the text, a
// synthetic mouseup with no position) it opens under the selection.
export const RELEASE_NEAR = 60;
// The popup's top-left from the release point: below it, a little left.
export const POINT_GAP = 12;
export const POINT_LEFT = 8;
// Between a selection's bottom edge and the popup.
export const RECT_GAP = 8;
// How far the mouse moves from where it was released before a mouse
// selection's popup opens (px): a hand still on the mouse sends a move or
// two of a pixel.
export const MOVE_TO_SHOW = 3;

// The release point `point` ({x, y}) when it lies within RELEASE_NEAR of
// the selection's bounding `rect` ({top, left, width, bottom}), else null.
export function nearRelease(point, rect) {
  if (!point || !rect || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
  const right = rect.left + rect.width;
  const dx = Math.max(rect.left - point.x, 0, point.x - right);
  const dy = Math.max(rect.top - point.y, 0, point.y - rect.bottom);
  return dx <= RELEASE_NEAR && dy <= RELEASE_NEAR ? { x: point.x, y: point.y } : null;
}

// The anchor for the popup's state (PdfViewer selPopup): an area at its
// tip, a mouse selection at its release point, any other selection (touch,
// keyboard) under its bounding rect.
export function tipAnchor(popup) {
  if (popup.kind === "area") return { top: popup.tip.top, bottom: popup.tip.top, left: popup.tip.left, gap: 0 };
  if (popup.point) return { top: popup.point.y, bottom: popup.point.y, left: popup.point.x - POINT_LEFT, gap: POINT_GAP };
  return { top: popup.rect.top, bottom: popup.rect.bottom, left: popup.rect.left, gap: RECT_GAP };
}
