// Reorder by dragging (docs/dev/ui-design.md, docs/dev/handwriting.md
// "Reorder by dragging"): the one drag that moves an item along a row or
// down a list, for the handwriting strip's presets and Settings' bar lists.
//
// The caller marks each slot inside `containerRef` with data-order-id={id};
// their DOM order is the visible order. `sourceProps(id)` goes on what is
// pressed: with `start` "hold" the slot's own button, which a mouse drags
// past DRAG_PX and a finger or pen after HOLD_MS held (shared/ui/press.js's
// long press, with its HOLD_SLOP) and then moved, while a hold that never
// moved stays the button's ordinary click; with "handle" a grip button
// (touch-action: none, no click of its own) that any pointer drags past
// DRAG_PX. The pointer is captured (setPointerCapture) and `drag` follows
// it: { id, x, y, to, bar }, the client point for a ghost (the caller's
// `.dragGhost`), the visible slot under the pointer along `axis` (0 … the
// slot count: before the slot at that index, or last) and the 2 px
// `.dropBar` marking it, in the container's coordinates (a vertical bar on
// axis "x", a horizontal one on "y"). The release inside `bounds()` (the
// container's rect by default) calls onMove(id, to) and swallows the click
// the press would get; outside, Escape, a window blur or a pointercancel
// leaves the order, and outside `bar` is null, so no slot shows where a
// release would not land. onStatus says "Drop to reorder." as a drag begins.
//
// `keyProps(id)` moves the focused item a slot with the arrow keys along
// `axis` (Up / Down, or Left / Right), or to either end with Home / End,
// through the same onMove(id, to).
import { useCallback, useEffect, useRef, useState } from "react";
import { HOLD_MS, HOLD_SLOP, swallowClick } from "./press.js";
import { t } from "../i18n/i18n.js";

export const DRAG_PX = 6;

export function useDragOrder({ containerRef, axis = "x", start = "hold", onMove, onStatus, bounds }) {
  const [drag, setDrag] = useState(null);     // {id, x, y, to, bar: {left, top, width, height}} while live
  const press = useRef(null);                 // the press: {id, pointerId, el, x, y, timer, live, moved}
  const latest = useRef(null);
  latest.current = { onMove, onStatus, bounds };
  const end = useCallback(() => {
    const p = press.current;
    if (!p) return;
    clearTimeout(p.timer);
    try { p.el.releasePointerCapture(p.pointerId); } catch {}
    press.current = null;
    setDrag(null);
  }, []);
  const slots = () => [...(containerRef.current?.querySelectorAll("[data-order-id]") || [])];
  // The visible slot a point drops into, and where the bar for it goes (in
  // the container's own coordinates, as its absolute children are placed).
  const slotAt = (x, y) => {
    const box = containerRef.current;
    const rects = slots().map((el) => el.getBoundingClientRect());
    const down = axis === "y";
    let to = rects.findIndex((r) => (down ? y < r.top + r.height / 2 : x < r.left + r.width / 2));
    if (to < 0) to = rects.length;
    const r = rects[Math.min(to, rects.length - 1)];
    if (!r) return { to, bar: null };
    const c = box.getBoundingClientRect();
    const ox = c.left + box.clientLeft - box.scrollLeft, oy = c.top + box.clientTop - box.scrollTop;
    const edge = down ? (to < rects.length ? r.top : r.bottom) : (to < rects.length ? r.left : r.right);
    return {
      to,
      bar: down
        ? { left: Math.round(r.left - ox), top: Math.round(edge - oy) - 1, width: Math.round(r.width), height: 2 }
        : { left: Math.round(edge - ox) - 1, top: Math.round(r.top - oy), width: 2, height: Math.round(r.height) },
    };
  };
  const inside = (x, y) => {
    const area = latest.current.bounds ? latest.current.bounds() : containerRef.current?.getBoundingClientRect();
    return !!area && x >= area.left && x <= area.right && y >= area.top && y <= area.bottom;
  };
  // The drag at a point: the slot it would drop into, and its bar only
  // where a release there would move it.
  const dragAt = (id, x, y) => {
    const { to, bar } = slotAt(x, y);
    return { id, x, y, to, bar: inside(x, y) ? bar : null };
  };
  const begin = (p) => {
    p.live = true;
    latest.current.onStatus?.(t("Drop to reorder."));
    setDrag(dragAt(p.id, p.x, p.y));
  };
  useEffect(() => {
    const escape = (e) => { if (e.key === "Escape" && press.current?.live) { e.stopPropagation(); end(); } };
    window.addEventListener("keydown", escape, true);
    window.addEventListener("blur", end);
    return () => { window.removeEventListener("keydown", escape, true); window.removeEventListener("blur", end); end(); };
  }, [end]);
  const sourceProps = (id) => ({
    onPointerDown: (e) => {
      if (press.current || !e.isPrimary || (e.pointerType === "mouse" && e.button !== 0)) return;
      const p = { id, pointerId: e.pointerId, el: e.currentTarget, x: e.clientX, y: e.clientY, timer: 0, live: false, moved: false };
      press.current = p;
      try { e.currentTarget.setPointerCapture(e.pointerId); } catch {}
      if (start === "hold" && e.pointerType !== "mouse") p.timer = setTimeout(() => { if (press.current === p) begin(p); }, HOLD_MS);
    },
    onPointerMove: (e) => {
      const p = press.current;
      if (!p || p.pointerId !== e.pointerId) return;
      const d = Math.hypot(e.clientX - p.x, e.clientY - p.y);
      if (!p.live) {
        if (p.timer) { if (d > HOLD_SLOP) { clearTimeout(p.timer); p.timer = 0; } return; }  // a held finger that wandered: a plain press
        if ((start === "handle" || e.pointerType === "mouse") && d >= DRAG_PX) begin(p); else return;
      }
      if (d >= DRAG_PX) p.moved = true;
      setDrag(dragAt(p.id, e.clientX, e.clientY));
    },
    onPointerUp: (e) => {
      const p = press.current;
      if (!p || p.pointerId !== e.pointerId) return;
      if (!p.live || !p.moved) { end(); return; }   // a click, or a hold that never moved: the button's own tap
      swallowClick();
      const lands = inside(e.clientX, e.clientY);
      const { to } = slotAt(e.clientX, e.clientY);
      end();
      if (lands) latest.current.onMove?.(p.id, to);
    },
    onPointerCancel: (e) => { if (press.current?.pointerId === e.pointerId) { if (press.current.live) swallowClick(); end(); } },
    onLostPointerCapture: (e) => { if (press.current?.pointerId === e.pointerId && !press.current.live) end(); },
  });
  // One slot back or on, or to an end; a key that would not move it is left
  // to the page.
  const keyProps = (id) => ({
    onKeyDown: (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const ids = slots().map((el) => el.dataset.orderId);
      const from = ids.indexOf(String(id));
      if (from < 0) return;
      const [back, on] = axis === "y" ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
      const to = { [back]: from - 1, [on]: from + 2, Home: 0, End: ids.length }[e.key];
      if (to === undefined || to < 0 || to > ids.length || to === from || to === from + 1) return;
      e.preventDefault();
      latest.current.onMove?.(id, to);
    },
  });
  return { sourceProps, keyProps, drag };
}
