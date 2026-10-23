// The page's ink by surface (docs/dev/handwriting.md "Client"): its ink
// groups, each surface's (a PDF page's by their position's page, a
// sheet's by its id), the surface a paste without a point lands on, and
// what messages call a surface (the pure parts in inkSurfaces.js). App
// composes it with the page's blocks and sheets and the viewer's state.
import { useCallback, useEffect, useMemo, useRef } from "react";
import { inkBySheet } from "../notebook/notebook.js";
import { flattenBlocks } from "../shared/model/blockModel.js";
import { groupsBySurface, pasteTargetOf, surfaceLabel } from "./inkSurfaces.js";

// The part of a surface on the screen, in its page points ({x, y, width,
// height}): its box (a PDF page's .pdfPageWrap, a sheet's .nbSheet) cut
// to its viewer and the window; null when none of it shows.
export function visiblePart(page, size) {
  const el = typeof page === "string"
    ? document.querySelector(`.nbViewer .nbSheet[data-sheet-id="${CSS.escape(page)}"]`) || document.querySelector(`.nbSheet[data-sheet-id="${CSS.escape(page)}"]`)
    : document.querySelector(`.pdfPageWrap[data-page="${page}"]`);
  const r = el?.getBoundingClientRect();
  if (!r?.width || !size?.width) return null;
  const view = el.closest(".pdfViewer, .nbViewer")?.getBoundingClientRect();
  const left = Math.max(r.left, view?.left ?? 0, 0), top = Math.max(r.top, view?.top ?? 0, 0);
  const right = Math.min(r.right, view?.right ?? Infinity, window.innerWidth), bottom = Math.min(r.bottom, view?.bottom ?? Infinity, window.innerHeight);
  if (right <= left || bottom <= top) return null;
  const k = r.width / size.width;
  return { x: (left - r.left) / k, y: (top - r.top) / k, width: (right - left) / k, height: (bottom - top) / k };
}

// blocks: the page's tree. nbSheets: its sheets (notebook/notebook.js
// sheetsOf), and nbSheetsRef the same, read when a handler runs.
// nbCurrent: the notebook view's current sheet; notebook: that view is
// on. pdfUrl, pdfHidden: the viewer's PDF, and whether it is closed.
export function useInkSurfaces({ blocks, nbSheets, nbSheetsRef, nbCurrent, notebook, pdfUrl, pdfHidden }) {
  // The page's ink groups as {id, properties}, the identity-stable list
  // the page marks are built from: the same array while their JSON is.
  const prevInkRef = useRef({ json: "", value: [] });
  const inkBlocks = useMemo(() => {
    const next = flattenBlocks(blocks).filter((b) => b.properties?.ink_url !== undefined)
      .map((b) => ({ id: b.id, properties: b.properties }));
    const json = JSON.stringify(next);
    if (json === prevInkRef.current.json) return prevInkRef.current.value;
    prevInkRef.current = { json, value: next };
    return next;
  }, [blocks]);
  // A sheet's groups by its id (notebook/notebook.js inkBySheet).
  const nbInk = useMemo(() => (nbSheets.length ? inkBySheet(blocks) : new Map()), [nbSheets, blocks]);
  // Each surface's groups: a PDF page's by their position's page, a
  // sheet's by its id.
  const inkBySurface = useMemo(() => groupsBySurface(inkBlocks, nbInk), [inkBlocks, nbInk]);
  // The PDF page in view and its size at scale 1 (PdfViewer's onPageInView:
  // the page whose centre lies nearest the scroller's), where Ctrl+V and
  // the pill's Paste land; a notebook pastes on nbCurrent. Another
  // document forgets it: until its viewer reports a page of its own (once
  // it is laid out), nothing pastes onto a page of the last one.
  const pageInViewRef = useRef(null);
  const onPageInView = useCallback((page, size) => { pageInViewRef.current = { page, size }; }, []);
  useEffect(() => { pageInViewRef.current = null; }, [pdfUrl]);
  // Where a paste without a point goes: the notebook's current sheet, else
  // the PDF page in view, with its size.
  function pasteTarget() {
    return pasteTargetOf({ notebook, sheets: nbSheetsRef.current, current: nbCurrent, inView: pageInViewRef.current, pdfUrl, pdfHidden });
  }
  // What a stroke's `page` is called in messages: a PDF page's number, a
  // notebook sheet's place among the sheets.
  function inkPageNumber(page) {
    return surfaceLabel(page, nbSheetsRef.current);
  }
  return { inkBlocks, inkBySurface, onPageInView, pasteTarget, inkPageNumber };
}
