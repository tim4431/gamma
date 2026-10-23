// A surface that leaves with motion (docs/dev/ui-design.md, "Motion").
// React takes a closed menu, popover or dialog out of the page at once;
// useLeave puts a still copy of it where it stood, and the copy plays its
// kind's way out (motion.css, [data-leaving]) and then goes. The copy is
// plain DOM outside React: inert, hidden from assistive technology and
// from the pointer, so nothing in it answers a press or a key and the
// next surface can open over it at once. Code that asks whether a surface
// is open skips the copies (`:not([data-leaving])`).
import { useCallback, useRef } from "react";
import { reducedMotion } from "./motion.js";


// The longest a copy stays when its animation never reports its end (a
// tab in the background, a kind without a recipe).
const LONGEST_MS = 1000;

// A surface that arrives takes the place of any copy of its kind still on
// its way out, as a native menu does: a menu opened as another closes, the
// tasks panel taking over from the More sheet, a dialog opened from one
// that closed. So only one of a kind is ever seen leaving, and never while
// another stands. `arrivals` counts each kind's arrivals; `leaving` holds
// its copies in the page.
const arrivals = new Map();
const leaving = new Map();
function arrive(kind) {
  arrivals.set(kind, (arrivals.get(kind) || 0) + 1);
  for (const copy of leaving.get(kind) || []) copy.remove();
  leaving.delete(kind);
}

// What a copy must not carry: an id or a guide anchor would let something
// find the copy in place of a live surface.
const DROP = ["id", "data-guide", "data-tour", "autofocus"];

// A copy of `node` as it stands. cloneNode takes the markup; this adds the
// state the markup does not hold: what a field holds, how far something is
// scrolled (kept to apply once the copy is in the page, where scrolling
// means something), what a canvas shows. A frame or a media element would
// load again, so it leaves an empty box of its size instead.
export function stillCopy(node) {
  const copy = node.cloneNode(true);
  const from = [node, ...node.querySelectorAll("*")];
  const to = [copy, ...copy.querySelectorAll("*")];
  const scrolls = [];
  from.forEach((a, i) => {
    const b = to[i];
    for (const name of DROP) b.removeAttribute(name);
    if (a.scrollTop || a.scrollLeft) scrolls.push([b, a.scrollTop, a.scrollLeft]);
    const tag = a.localName;
    if (tag === "input" || tag === "textarea" || tag === "select") {
      b.value = a.value;
      if ("checked" in a) b.checked = a.checked;
    } else if (tag === "canvas" && a.width && a.height) {
      try { b.getContext("2d")?.drawImage(a, 0, 0); } catch { /* an unreadable canvas stays blank */ }
    } else if (tag === "iframe" || tag === "video" || tag === "audio") {
      const box = node.ownerDocument.createElement("span");
      box.style.cssText = `display:inline-block;width:${a.offsetWidth}px;height:${a.offsetHeight}px`;
      b.replaceWith(box);
    }
  });
  copy.setAttribute("aria-hidden", "true");
  copy.setAttribute("inert", "");
  return { copy, scrolls };
}

// Leaves `node`, which React is about to take out of the page, through a
// copy that plays `kind`'s way out. Called while `node` still stands; the
// copy goes in once it has gone, in a microtask, so before the browser
// paints a frame without it. Nothing happens when `node` stays (its ref was
// handed over, StrictMode mounted it again), when its place went with it
// (the button its popover hung from closed too), when it was not shown, or
// when another of its kind has arrived meanwhile (in the same commit).
export function leave(node, kind) {
  if (!node?.isConnected || reducedMotion() || node.ownerDocument.hidden) return;
  const style = getComputedStyle(node);
  if (style.visibility === "hidden" || !node.getClientRects().length) return;
  const parent = node.parentNode, next = node.nextSibling;
  const arrived = arrivals.get(kind) || 0;
  const { copy, scrolls } = stillCopy(node);
  copy.dataset.leaving = kind;
  // A surface in the flow (a toast in its stack) leaves from where it
  // stood without holding its room: the ones after it close up at once.
  if (style.position === "static" || style.position === "relative") {
    Object.assign(copy.style, {
      position: "absolute", boxSizing: "border-box", margin: "0",
      left: `${node.offsetLeft}px`, top: `${node.offsetTop}px`,
      width: `${node.offsetWidth}px`, height: `${node.offsetHeight}px`,
    });
  }
  queueMicrotask(() => {
    if (node.isConnected || !parent.isConnected || (arrivals.get(kind) || 0) !== arrived) return;
    parent.insertBefore(copy, next?.parentNode === parent ? next : null);
    for (const [el, top, left] of scrolls) { el.scrollTop = top; el.scrollLeft = left; }
    if (!leaving.has(kind)) leaving.set(kind, new Set());
    leaving.get(kind).add(copy);
    const done = () => { copy.remove(); leaving.get(kind)?.delete(copy); };
    copy.addEventListener("animationend", (e) => { if (e.target === copy) done(); });
    setTimeout(done, LONGEST_MS);
  });
}

// A callback ref for a surface's root element: `ref={useLeave("surface")}`.
// `kind` names its way out in motion.css: "surface" (a menu, a popover),
// "dialog" (a backdrop and the dialog on it), "toast". Its arrival takes
// the place of a copy of its kind still leaving. Hand it a ref of your own
// (an object or a function) to keep that one set as well.
export function useLeave(kind = "surface", ref = null) {
  const shown = useRef(null);
  return useCallback((node) => {
    if (typeof ref === "function") ref(node);
    else if (ref) ref.current = node;
    if (node) { shown.current = node; arrive(kind); return; }
    const gone = shown.current;
    shown.current = null;
    if (gone) leave(gone, kind);
  }, [kind, ref]);
}
