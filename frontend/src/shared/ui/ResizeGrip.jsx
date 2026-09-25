// The edge drag grips that size a rendered figure (a note image, a Mermaid
// diagram) — one behavior for every resizable thing in the notes, Notion's:
// a grip on each side, and since figures are centred both edges move
// together, so the width changes by twice the pointer's travel and the
// dragged grip stays under the pointer. The drag writes a pixel width through
// onCommit when the pointer is released, double-click clears it back to the
// natural size (onCommit(0)), and a click that never moved commits nothing.
// The live width during the drag is returned so the figure can follow the
// pointer before the source changes. `bound` (optional) returns the element
// the figure sits in; its content width caps the drag.
import React, { useRef, useState } from "react";
import { t } from "../../shared/i18n/i18n.js";

function contentWidth(el) {
  if (!el) return Infinity;
  const cs = getComputedStyle(el);
  return el.clientWidth - parseFloat(cs.paddingLeft || 0) - parseFloat(cs.paddingRight || 0);
}

export function useDragResize({ measure, onCommit, bound, min = 60, max = 1600 }) {
  const [dragW, setDragW] = useState(null);
  const dragRef = useRef(null); // {startX, startW, dir, cap, w, moved}
  const stop = (e) => e.stopPropagation();
  function start(e, dir) {
    e.preventDefault();
    e.stopPropagation();
    const cap = Math.max(min, Math.min(max, Math.floor(contentWidth(bound?.()))));
    dragRef.current = { startX: e.clientX, startW: measure() || 200, dir, cap, w: null, moved: false };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }
  function move(e) {
    const d = dragRef.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.startX) > 2) d.moved = true;
    d.w = Math.round(Math.min(d.cap, Math.max(min, d.startW + 2 * d.dir * (e.clientX - d.startX))));
    setDragW(d.w);
  }
  function end() {
    const d = dragRef.current;
    dragRef.current = null;
    setDragW(null);
    if (d?.moved && d.w) onCommit(d.w);
  }
  const gripProps = (side) => ({
    className: `mdResizeGrip ${side}`,
    title: t("Drag to resize · double-click for natural size"),
    onMouseDown: stop,
    onClick: stop,
    onPointerDown: (e) => start(e, side === "left" ? -1 : 1),
    onPointerMove: move,
    onPointerUp: end,
    onPointerCancel: end,
    onDoubleClick: (e) => { e.stopPropagation(); onCommit(0); },
  });
  return { dragW, gripProps };
}

// Both sides' grips; `as` picks span (inline figures) or div.
export function ResizeGrips({ as: Tag = "span", gripProps }) {
  return <><Tag {...gripProps("left")} /><Tag {...gripProps("right")} /></>;
}
