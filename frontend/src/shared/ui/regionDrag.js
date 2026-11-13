// The one region drag (docs/dev/ui-design.md "The PDF viewer", "The image
// view"): a rectangle dragged over a box, the PDF page's area note and the
// picture's region alike, so the two never drift apart. The caller decides
// whether a press starts one (its key, its mode, what it landed on) and
// draws the rubber band from `onMove`; this takes the pointer from there:
// the primary pointer alone, document-level move and up listeners so the
// drag survives leaving the box, the rectangle clamped to the box and
// coalesced to one `onMove` per frame (a high-rate pointer outpaces frames,
// and each move re-renders the caller), a second finger cancels it (the
// gesture became a pinch), under a pen another pointer is a palm and
// changes nothing, a drag under `minPx` either way is a click and gives
// nothing, and the click the release would deliver to whatever lies under
// it is swallowed (shared/ui/press.js).
//
//   beginRegionDrag(e, { box, onMove, onDone, minPx })
//     e        the pointerdown
//     box()    the box's DOMRect, read as the drag starts
//     onMove(rect | null)   the live rectangle in the box's px; null when
//              the drag ends or is cancelled
//     onDone(rect, box)     the rectangle on release, past minPx
//   Returns whether a drag began.
import { swallowClick } from "./press.js";
import { MIN_DRAG_PX, dragRect, isClick } from "./regionGeometry.js";

export function beginRegionDrag(e, { box, onMove, onDone, minPx = MIN_DRAG_PX }) {
  if (e.button !== 0 || !e.isPrimary) return false;
  const rect = box();
  if (!rect) return false;
  e.preventDefault(); // keep a text layer from starting a selection
  const pointerId = e.pointerId;
  // ui-allow pointer-kind: the pen's palm rule lives here for both viewers (a finger beside a pen is a palm, as the ink layer reads it)
  const pen = e.pointerType === "pen";
  const sx = e.clientX, sy = e.clientY;
  const toRect = (cx, cy) => dragRect(sx, sy, cx, cy, rect);
  let moveRaf = 0, moveX = 0, moveY = 0;
  const detach = () => {
    cancelAnimationFrame(moveRaf);
    document.removeEventListener("pointerdown", onOther, true);
    document.removeEventListener("pointermove", onMove_);
    document.removeEventListener("pointerup", onUp, true);
    document.removeEventListener("pointercancel", onCancel);
  };
  function onMove_(ev) {
    if (ev.pointerId !== pointerId) return;
    moveX = ev.clientX;
    moveY = ev.clientY;
    if (moveRaf) return;
    moveRaf = requestAnimationFrame(() => {
      moveRaf = 0;
      onMove(toRect(moveX, moveY));
    });
  }
  function onCancel(ev) {
    if (ev.pointerId !== pointerId) return;
    detach();
    onMove(null);
  }
  function onOther(ev) {
    if (ev.pointerId !== pointerId && !pen) onCancel({ pointerId });
  }
  function onUp(ev) {
    if (ev.pointerId !== pointerId) return;
    detach();
    onMove(null);
    const r = toRect(ev.clientX, ev.clientY);
    if (isClick(r, minPx)) return;
    swallowClick();
    onDone(r, rect);
  }
  document.addEventListener("pointerdown", onOther, true);
  document.addEventListener("pointermove", onMove_);
  document.addEventListener("pointerup", onUp, true);
  document.addEventListener("pointercancel", onCancel);
  return true;
}
