// A pen's text selection on a PDF page, with no ink tool armed
// (docs/dev/handwriting.md "Claiming input"). A mouse's drag selects by itself; a
// stylus's only pans the page, so the drag is read here from its pointer
// events and the selection set from the caret under it. One path for the
// iPad, Android and a desktop's pen. InkLayer.jsx decides the press is the
// page's (onPenDrag) and holds the touch: its pan off, fingers as palms.
import { caretAt } from "../shared/lib/caret.js";
import { penLifted } from "../shared/ui/press.js";

// How far the pen goes before a press is a drag (css px), as InkLayer tells
// a tap from a swipe: a tap selects nothing and still clicks what it lands on.
const DRAG_PX = 8;

// The text under a point, of a page's text layer or a translation's: {node,
// offset}, or null off the text (over a gap the browsers answer the top of
// the layer, which would throw the selection up the page).
function textAt(x, y) {
  const at = caretAt(x, y);
  return at?.node.nodeType === Node.TEXT_NODE && at.node.parentElement?.closest(".textLayer, .pdfTransText") ? at : null;
}

// The press in progress ends; a new one takes its place.
let stopDrag = null;

// Whether a pen is at it: the selection changes as it goes, and only its
// release ends it, as a mouse's does.
export const penSelecting = () => !!stopDrag;

// `press`: the pen's pointerdown; `viewer`: the pages' .pdfViewer;
// `onSelected(released)`: the drag ended with something selected, by the
// pen's lift or, not `released`, by the system. A lift that never came ends
// it too, where the pen last was (penLifted: the pen hovering or down
// again), so the page's marks never stay deaf to every pointer. The caret
// comes from a hit test, which stops at whatever lies over the text (a
// highlight, a link box, ink), so while the page answers one only its text
// does (data-pen-select, app.css). Not before the press is a drag, so that
// a tap still lands on them.
export function beginPenSelect(press, viewer, onSelected) {
  stopDrag?.();
  press.preventDefault(); // a desktop's pen: no selection of the mouse's beside this one
  const { pointerId, clientX: startX, clientY: startY } = press;
  const textOnly = (on) => {
    if (on) viewer.dataset.penSelect = "";
    else delete viewer.dataset.penSelect;
  };
  textOnly(true);
  let anchor = textAt(startX, startY);
  textOnly(false);
  let x = startX, y = startY, dragging = false, selected = false, raf = 0;
  const extend = () => {
    const at = textAt(x, y);
    if (!at) return;
    if (!anchor?.node.isConnected) anchor = at;
    window.getSelection()?.setBaseAndExtent(anchor.node, anchor.offset, at.node, at.offset);
    selected = true;
  };
  const stop = () => {
    stopDrag = null;
    cancelAnimationFrame(raf);
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerdown", onAgain, true);
    document.removeEventListener("pointerup", onUp, true);
    document.removeEventListener("pointercancel", onCancel, true);
    textOnly(false);
  };
  function onMove(e) {
    if (penLifted(e, pointerId)) { lift(); return; }
    if (e.pointerId !== pointerId) return;
    x = e.clientX;
    y = e.clientY;
    if (!dragging) {
      if (Math.hypot(x - startX, y - startY) <= DRAG_PX) return;
      dragging = true;
      textOnly(true);
    }
    if (!raf) raf = requestAnimationFrame(() => { raf = 0; extend(); });
  }
  function onUp(e) {
    if (e.pointerId !== pointerId) return;
    x = e.clientX;
    y = e.clientY;
    lift();
  }
  // the lift, at (x, y)
  function lift() {
    if (dragging) extend();
    stop();
    if (selected) onSelected(true);
  }
  function onAgain(e) {
    if (penLifted(e, pointerId)) lift();
  }
  // The system took the touch (a pinch, a scroll already running): what was
  // selected stays.
  function onCancel(e) {
    if (e.pointerId !== pointerId) return;
    stop();
    if (selected) onSelected(false);
  }
  stopDrag = stop;
  document.addEventListener("pointermove", onMove);
  document.addEventListener("pointerdown", onAgain, true);
  document.addEventListener("pointerup", onUp, true);
  document.addEventListener("pointercancel", onCancel, true);
}
