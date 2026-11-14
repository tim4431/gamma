// The fixed frame a selection popup stands in (pdf/PdfViewer.jsx's PlainTip,
// the code view's TextTip, a picture's RegionTip; their buttons are
// SelectionTip.jsx's): under the selection, or above it when the popup
// (a long translation) would run past the bottom of the window; kept inside
// the window. `anchor` is {top, bottom, left, gap} in viewport px
// (pdf/selectionTip.js tipAnchor; a drawn region's box). Placed again in
// the frame its size changes (placement.js), so a growing translation
// never shows a frame of itself still under the selection, past the
// window's bottom. Imports no viewer, so the code view's chunk takes it
// without pdf.js.
import React, { useRef } from "react";
import { placeFloating, useFloatingPosition } from "./placement.js";

export function TipFrame({ anchor, children }) {
  const ref = useRef(null);
  const below = anchor.bottom + anchor.gap;
  const pos = useFloatingPosition(ref, (el) => placeFloating(el.getBoundingClientRect(), { x: anchor.left, y: below, flipY: anchor.top - anchor.gap }),
    [anchor.top, below, anchor.left]);
  return <div ref={ref} style={{ position: "fixed", zIndex: "var(--z-tip)", ...(pos || { top: below, left: anchor.left, visibility: "hidden" }) }}>{children}</div>;
}
