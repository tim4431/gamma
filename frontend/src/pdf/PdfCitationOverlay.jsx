import React, { useLayoutEffect, useRef, useState } from "react";
import { citationRects } from "./pdfCitation.js";
import { t } from "../shared/i18n/i18n.js";
import { guideEvents } from "../guide/events.js";

export function PdfCitationOverlay({ citation, wrapRef, ready }) {
  const [result, setResult] = useState(null);
  const scrolled = useRef(null);
  useLayoutEffect(() => {
    setResult(null);
    // A citation without a quote just points at a page (the viewer already
    // scrolled there) — nothing to locate, nothing to mark.
    if (!citation?.quote || !ready || !wrapRef.current) return;
    const frame = requestAnimationFrame(() => {
      const found = citationRects(ready.runs, wrapRef.current, citation.quote);
      setResult(found);
      if (found.rects.length && scrolled.current !== citation) {
        scrolled.current = citation;
        guideEvents.emit("citation.shown");
        const viewer = wrapRef.current.closest(".pdfViewer");
        const box = wrapRef.current.getBoundingClientRect();
        if (viewer) {
          const view = viewer.getBoundingClientRect();
          const left = box.left + Math.min(...found.rects.map(r => r.left)) / 100 * box.width;
          const right = box.left + Math.max(...found.rects.map(r => r.left + r.width)) / 100 * box.width;
          const shift = left < view.left + 20 || right - left > viewer.clientWidth - 40
            ? left - view.left - 20 : Math.max(0, right - view.left - viewer.clientWidth + 20);
          viewer.scrollTo({ top: viewer.scrollTop + box.top
            + found.rects[0].top / 100 * box.height - view.top - 80,
            left: viewer.scrollLeft + shift, behavior: "auto" });
        }
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [citation, ready, wrapRef]);
  if (!citation?.quote || !result) return null;
  return <>
    {result.rects.map((r, i) => <div key={i} className="pdfCitationMark" aria-hidden="true" data-guide={i === 0 ? "pdf.citation" : undefined}
      style={{ left: `${r.left}%`, top: `${r.top}%`, width: `${r.width}%`, height: `${r.height}%` }} />)}
    {(result.status !== "matched" || result.approximate) && <div role="status" className="pdfCitationNotice">
      {result.approximate ? t("Highlighted an approximate text match.")
        : result.status === "ambiguous" ? t("More than one passage on this page matches this quote.")
        : t("Opened the cited page; the quote could not be located in its text layer.")}
    </div>}
  </>;
}
