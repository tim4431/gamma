import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

// The share of the window a zoomed picture may take (app.css .lightbox img).
const FIT_W = 0.94;
const FIT_H = 0.9;

// A vector picture's size when it fills the lightbox: its shown size,
// scaled up or down to the largest that fits the window.
export function lightboxFit(width, height) {
  const scale = Math.min(window.innerWidth * FIT_W / width, window.innerHeight * FIT_H / height);
  return { width: width * scale, height: height * scale };
}

// A picture zoomed over the page (a note image, a diagram): a click
// anywhere or Escape closes it. The overlay sits in a portal but React
// still bubbles its events through the tree that opened it, so they stop
// here: a press on it never selects the note object or opens the block
// editor underneath. Escape is caught first and closes only the lightbox.
export function Lightbox({ src, alt = "", caption = "", onClose }) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      close.current();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);
  const stop = (e) => e.stopPropagation();
  return createPortal(
    <div className="lightbox" onPointerDown={stop} onPointerUp={stop} onMouseDown={stop}
      onDoubleClick={stop} onContextMenu={stop}
      onClick={(e) => { e.stopPropagation(); close.current(); }}>
      <img src={src} alt={alt} draggable={false} />
      {caption ? <div className="lightboxCaption">{caption}</div> : null}
    </div>,
    document.body,
  );
}
