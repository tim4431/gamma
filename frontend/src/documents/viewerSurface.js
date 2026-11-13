// The surface App hands the document panes of its own (DocumentPane.jsx's
// text, picture, player and file viewers; docs/dev/documents.md "Viewers"):
// one object built from App's state and handlers, and the place a viewer
// reports where it stands through. App only wires: the handlers reach the
// viewer through stable closures that read App's latest, so the surface
// changes with what the viewer shows and never with a render of App alone.
// Beside it, what the paperclip's Replace accepts for a document.
import { useMemo, useRef } from "react";
import { EXTENSIONS } from "./formats.js";

// The handlers a surface carries, each null when App gives none (the
// viewer then leaves the control out: no region drag when read-only, no
// Show in folder off the desktop).
const HANDLERS = ["onZoomTo", "onEffectiveScale", "onPlace", "onRegion", "onRegionJump", "onRegionContext", "onRegionThread",
  "onMarginLost", "onAskRegion", "onReveal"];

// The extensions a replacement of `doc` may bring, for the file picker's
// accept: the registry rows of the document's format (formats.js), "" for
// no document or an unknown format (the picker then takes any file).
export function replaceAccept(doc) {
  if (!doc?.format) return "";
  return Object.entries(EXTENSIONS).filter(([, row]) => row.format === doc.format).map(([ext]) => ext).join(",");
}

// `state`: {doc, fileUrl, pageId, readOnly, hidden, phone, scale (App's
// pdfScale), regions, anchors, flashId, regionMode, commentMargin,
// marginRef, scrollRef, captureRef}; `handlers`: HANDLERS' functions, or
// null, and `placeOf()`, where the page's viewer last stood (read once per
// page: a viewer takes its place as it mounts). → {surface, reportRef}:
// the surface, memoised on what it shows, and the ref the viewer sets to
// report its place (App's captureScrollPos calls it before leaving).
export function useViewerSurface(state, handlers) {
  const reportRef = useRef(null);
  const fns = useRef(handlers);
  fns.current = handlers;
  const given = HANDLERS.map((k) => !!handlers[k]);
  const { doc, fileUrl, pageId, readOnly, hidden, phone, scale, regions, anchors, flashId, regionMode, commentMargin,
    marginRef, scrollRef, captureRef } = state;
  const surface = useMemo(() => {
    const call = (k) => (fns.current[k] ? (...args) => fns.current[k]?.(...args) : null);
    return {
      doc, fileUrl, pageId, readOnly, hidden, phone,
      scale: { value: scale, onZoomTo: call("onZoomTo"), onEffectiveScale: call("onEffectiveScale") },
      place: fns.current.placeOf?.() ?? null, onPlace: call("onPlace"), reportRef,
      regions, anchors, flashId, regionMode,
      onRegion: call("onRegion"), onRegionJump: call("onRegionJump"), onRegionContext: call("onRegionContext"), onRegionThread: call("onRegionThread"),
      commentMargin, marginRef, onMarginLost: call("onMarginLost"), scrollRef, captureRef,
      onAskRegion: call("onAskRegion"), onReveal: call("onReveal"),
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `given` stands for the handlers' presence
  }, [doc, fileUrl, pageId, readOnly, hidden, phone, scale, regions, anchors, flashId, regionMode, commentMargin,
    marginRef, scrollRef, captureRef, ...given]);
  return { surface, reportRef };
}
