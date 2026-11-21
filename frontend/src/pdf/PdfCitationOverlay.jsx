import React, { useLayoutEffect, useRef } from "react";
import { citationMarks } from "./pdfCitation.js";
import { t } from "../shared/i18n/i18n.js";
import { guideEvents } from "../guide/events.js";

// A citation's passage on its page: the rectangles the server found
// (citation.place, pdf/pdfCitation.js) as percentages of the page, so a zoom
// carries them, and a notice when the match is not exact or there is none.
export function PdfCitationOverlay({ citation, wrapRef }) {
  const place = citation?.quote ? citation.place : null;
  const scrolled = useRef(null);
  useLayoutEffect(() => {
    const marks = citationMarks(place);
    if (!marks.length || !wrapRef.current || scrolled.current === citation) return;
    const frame = requestAnimationFrame(() => {
      if (!wrapRef.current) return;
      scrolled.current = citation;
      guideEvents.emit("citation.shown");
      const viewer = wrapRef.current.closest(".pdfViewer");
      const box = wrapRef.current.getBoundingClientRect();
      if (!viewer) return;
      const view = viewer.getBoundingClientRect();
      const left = box.left + Math.min(...marks.map(r => r.left)) / 100 * box.width;
      const right = box.left + Math.max(...marks.map(r => r.left + r.width)) / 100 * box.width;
      const shift = left < view.left + 20 || right - left > viewer.clientWidth - 40
        ? left - view.left - 20 : Math.max(0, right - view.left - viewer.clientWidth + 20);
      viewer.scrollTo({ top: viewer.scrollTop + box.top + marks[0].top / 100 * box.height - view.top - 80,
        left: viewer.scrollLeft + shift, behavior: "auto" });
    });
    return () => cancelAnimationFrame(frame);
  }, [citation, place, wrapRef]);
  if (!place) return null;
  const approximate = place.status === "matched" && place.match < 1;
  return <>
    {citationMarks(place).map((r, i) => <div key={i} className="pdfCitationMark" aria-hidden="true" data-guide={i === 0 ? "pdf.citation" : undefined}
      style={{ left: `${r.left}%`, top: `${r.top}%`, width: `${r.width}%`, height: `${r.height}%` }} />)}
    {(place.status !== "matched" || approximate) && <div role="status" className="pdfCitationNotice">
      {approximate ? t("Highlighted an approximate text match ({n}% alike).", { n: Math.round(place.match * 100) })
        : place.status === "ambiguous" ? t("More than one passage on this page matches this quote.")
        : t("Opened the cited page; the quote could not be found on it or the pages beside it.")}
    </div>}
  </>;
}
