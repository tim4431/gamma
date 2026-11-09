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

// A mouse selection's popup waiting for the hand to move on: `wait(tip)`
// holds a tip whose `point` is the release, `move(x, y)` reports a move with
// no button held, and the tip goes to `open` once the mouse is MOVE_TO_SHOW
// from its release. The release is read a timer after the mouseup, so a
// move made in between counts. `press` (a mousedown) drops both, `drop` the
// tip alone.
export function tipWaiter(open) {
  let waiting = null;
  let pointer = null; // where the mouse has moved since its last press
  const check = () => {
    if (!waiting || !pointer || Math.hypot(pointer.x - waiting.point.x, pointer.y - waiting.point.y) < MOVE_TO_SHOW) return;
    const tip = waiting;
    waiting = null;
    open(tip);
  };
  return {
    wait(tip) { waiting = tip; check(); },
    move(x, y) { pointer = { x, y }; check(); },
    press() { waiting = pointer = null; },
    drop() { waiting = null; },
  };
}

// The anchor for the popup's state (PdfViewer selPopup): an area at its
// tip, a mouse selection at its release point, any other selection (touch,
// a pen, keyboard) under its bounding rect: what is seen selected (`shown`,
// a translation's reflowed text), else the rect it keeps (`rect`).
export function tipAnchor(popup) {
  if (popup.kind === "area") return { top: popup.tip.top, bottom: popup.tip.top, left: popup.tip.left, gap: 0 };
  if (popup.point) return { top: popup.point.y, bottom: popup.point.y, left: popup.point.x - POINT_LEFT, gap: POINT_GAP };
  const r = popup.shown || popup.rect;
  return { top: r.top, bottom: r.bottom, left: r.left, gap: RECT_GAP };
}

// Whether the document's selection `sel` is a PDF page's text, the
// selection the popup is offered for: a range that is not collapsed,
// starting inside a `.textLayer`, or with an end in a translation's text
// (`.pdfTransText`), which stands for the original's blocks under it.
export function inTextLayer(sel) {
  if (!sel || sel.isCollapsed || !sel.rangeCount) return false;
  const el = (node) => (node?.nodeType === 3 ? node.parentElement : node);
  const start = el(sel.getRangeAt(0).startContainer);
  return Boolean(start?.closest?.(".textLayer")
    || el(sel.anchorNode)?.closest?.(".pdfTransText") || el(sel.focusNode)?.closest?.(".pdfTransText"));
}
