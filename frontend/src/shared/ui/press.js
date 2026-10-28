// One request, two gestures (docs/dev/ui-design.md, "One behaviour for mouse
// and finger"). A right-click and a long press both ask for an item's menu;
// a double-click and a double tap both ask for its second action. A call
// site spreads one of these where it had onContextMenu or onDoubleClick and
// never learns which gesture it was:
//
//   <div {...menuPress(openPageMenu(id))}>      right-click, or a held finger
//   <span {...doublePress(toggleFold)}>         double-click, or a double tap
//   <button {...triggerPress(toggleMenu)}>      a press, or Enter / Space
//
// The browser cannot be left to it: iPadOS Safari sends no `contextmenu` for
// a long press, and its `dblclick` for a double tap is not dependable.

export const HOLD_MS = 500;
export const HOLD_SLOP = 10;    // px a finger may wander and still be holding
export const DOUBLE_MS = 400;   // between the two releases
export const DOUBLE_SLOP = 30;  // px between them
const ECHO_MS = 800;     // the browser's own event for a gesture already answered
const LIFT_CLICK_MS = 300; // the click a lifted finger sends: a task of its own, a few frames on a busy page

// What a menu opener reads from the mouse event, for a press that has none:
// the point, the element, and the two calls it makes.
export function pressAt(x, y, el) {
  return { clientX: x, clientY: y, currentTarget: el, target: el, preventDefault() {}, stopPropagation() {} };
}

// --- right-click, or a held finger ---------------------------------------------------

let hold = null;       // the finger being held: { press, timer, x, y, drag }
let held = -Infinity;  // when a hold last opened a menu

function endHold() {
  if (!hold) return;
  clearTimeout(hold.timer);
  // iPadOS lifts a draggable element at about the same moment a hold
  // completes, so the element is not draggable for the length of the press.
  if (hold.drag) hold.drag.draggable = true;
  for (const type of ["pointermove", "pointerup", "pointercancel"]) window.removeEventListener(type, watchHold, true);
  hold = null;
}

function watchHold(e) {
  if (e.type === "pointermove" && Math.hypot(e.clientX - hold.x, e.clientY - hold.y) < HOLD_SLOP) return;
  endHold();
}

// The finger lifts over the item whose menu just opened, and its click would
// do the item's own action (open the page). That one click goes nowhere. It
// comes after the finger lifts, in a task of its own, so it is waited for a
// moment (a menu just arriving keeps the page busy) and no longer: a click
// that never came must not take the next tap.
function swallowLiftClick() {
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  const done = () => setTimeout(() => document.removeEventListener("click", stop, true), LIFT_CLICK_MS);
  document.addEventListener("click", stop, { capture: true, once: true });
  window.addEventListener("pointerup", done, { capture: true, once: true });
  window.addEventListener("pointercancel", done, { capture: true, once: true });
}

// `open(event)` is the handler onContextMenu had. For a held finger it gets
// pressAt() of the finger's point.
export function menuPress(open) {
  if (!open) return {};
  return {
    "data-press": "menu",
    onContextMenu(e) {
      // Android sends its own contextmenu for the hold just answered.
      if (performance.now() - held < ECHO_MS) { e.preventDefault(); return; }
      endHold();
      open(e);
    },
    onPointerDown(e) {
      if (e.pointerType === "mouse" || !e.isPrimary) return;
      // One press reaches every element around the finger, innermost first:
      // a chip in a row keeps its own menu.
      const press = e.nativeEvent || e;
      if (hold?.press === press) return;
      endHold();
      const el = e.currentTarget, x = e.clientX, y = e.clientY;
      const drag = el.closest("[draggable='true']");
      if (drag) drag.draggable = false;
      const timer = setTimeout(() => {
        endHold();
        held = performance.now();
        swallowLiftClick();
        open(pressAt(x, y, el));
      }, HOLD_MS);
      hold = { press, timer, x, y, drag };
      for (const type of ["pointermove", "pointerup", "pointercancel"]) window.addEventListener(type, watchHold, true);
    },
  };
}

// --- a handled release --------------------------------------------------------------

// The click a handled pointer-up would deliver to whatever lies under it (a
// highlight rect, a link box, the button a drag started on) is not a click
// on that thing. Called from the pointer-up, it eats the one click that
// event dispatches next.
export function swallowClick() {
  const swallow = (ce) => { ce.stopPropagation(); ce.preventDefault(); };
  document.addEventListener("click", swallow, { capture: true, once: true });
  setTimeout(() => document.removeEventListener("click", swallow, { capture: true }), 0);
}

// --- a menu button ---------------------------------------------------------------------

// A button's menu opens on the press, as a native menu does, not on the
// click that follows. `run(event)` is called for a press with the primary
// button (a mouse's left button; a finger's or a pen's contact is button 0
// too), and for a click that no press led to (`detail` 0: Enter or Space on
// the focused button). The click after a press belongs to the press, which
// was already answered. useMenuTrigger (Menus.jsx) is built on it.
export function triggerPress(run) {
  return {
    onPointerDown(e) { if (e.button === 0) run(e); },
    onClick(e) { if (e.detail === 0) run(e); },
  };
}

// --- double-click, or a double tap ---------------------------------------------------

let tap = null;          // the last finger release: { el, t, x, y }
let tapped = -Infinity;  // when a double tap last ran
let counted = null;      // the release already counted, by the innermost element it reached

export function isDoubleTap(first, second) {
  return !!first && first.el === second.el && second.t - first.t < DOUBLE_MS
    && Math.hypot(second.x - first.x, second.y - first.y) < DOUBLE_SLOP;
}

// `run(event)` is the handler onDoubleClick had. A mouse keeps the browser's
// dblclick and the system's double-click speed; a finger's two releases are
// counted here.
export function doublePress(run) {
  if (!run) return {};
  return {
    onDoubleClick(e) {
      if (e.timeStamp - tapped < ECHO_MS) return;
      run(e);
    },
    onPointerUp(e) {
      const release = e.nativeEvent || e;
      if (e.pointerType === "mouse" || counted === release) return;
      counted = release;
      const next = { el: e.currentTarget, t: e.timeStamp, x: e.clientX, y: e.clientY };
      if (!isDoubleTap(tap, next)) { tap = next; return; }
      tap = null;
      tapped = e.timeStamp;
      run(e);
    },
  };
}

// --- a dialog's backdrop -------------------------------------------------------

// `dismiss(event)` closes the dialog for a press that went down and came up
// on the backdrop itself. The click alone cannot say so: it lands on the
// nearest element both ends share, so a drag that starts in the dialog (a
// field's text being selected) and is let go past its edge "clicks" the
// backdrop. Every backdrop reads the same two targets, so nested ones agree.
let downAt = null;
let upAt = null;

export function backdropPress(dismiss) {
  return {
    onPointerDown(e) { downAt = e.target; },
    onPointerUp(e) { upAt = e.target; },
    onClick(e) {
      if (e.target === e.currentTarget && downAt === e.currentTarget && upAt === e.currentTarget) dismiss(e);
    },
  };
}
