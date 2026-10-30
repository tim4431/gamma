// The translated view's overlay over a page (docs/dev/translation.md "The
// queue" and "Formulas"): a landed or streaming paragraph (TransPara), the
// formulas inside it (TransMath) and the wash over a paragraph still waiting
// (TransPending). PdfPage in pdf/PdfViewer.jsx lays them out from the page's
// entry (pdf/translateJob.js).
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { basePixels } from "./pageRaster.js";

// One translated paragraph. Two independent layers inside the block box:
// masks that cover EXACTLY the original text lines (per-line rects, so a
// figure the paragraph wraps around is never painted over), and the
// translated text with an inline background behind each rendered line
// (box-decoration-break: clone) — so text and mask stay readable even where
// the browser's line breaks don't coincide with the original's. The text is
// drawn at the original font size, shrunk in steps until it fits the box —
// the page layout never reflows. The measured shrink loop runs a handful of
// synchronous reflows per block; pages have tens of blocks, which is fine.
// A token in a translation: {vK} is the block's own K-th span, {v:pn.i.K}
// another block's (pdf/translatePlan.js results).
const TRANS_TOKEN = /\{v(?::(\d+)\.(\d+)\.)?(\d+)\}/g;

// A translated paragraph over its original lines: masks over exactly those
// lines, the translation in them, shrunk until it fits. Its tokens come back
// as the original's formulas (TransMath), citation markers and URLs.
export function TransPara({ box, lines, baseSize, text, streaming, block, page, scale, spanOf, basePaints }) {
  const ref = useRef(null); // the text container (the fit-measured element)
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    let size = baseSize;
    el.style.fontSize = size + "px";
    let guard = 0;
    while (guard++ < 24 && size > 6
        && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) {
      size *= 0.93;
      el.style.fontSize = size + "px";
    }
  }, [text, baseSize, box.width, box.height, basePaints]);
  const parts = [];
  let at = 0;
  for (const m of text.matchAll(TRANS_TOKEN)) {
    if (m.index > at) parts.push(text.slice(at, m.index));
    const pn = m[1] ? Number(m[1]) : page;
    const span = m[1] ? spanOf?.(pn, Number(m[2]), Number(m[3])) : block?.spans?.[Number(m[3]) - 1];
    if (span?.kind === "math") {
      parts.push(<TransMath key={m.index} span={span} page={pn} em={block.size} scale={scale} basePaints={basePaints} />);
    } else if (span) parts.push(span.str);
    at = m.index + m[0].length;
  }
  if (at < text.length) parts.push(text.slice(at));
  return (
    <div className={"pdfTransPara" + (streaming ? " streaming" : " landed")}
      style={{ left: box.left, top: box.top, width: box.width, height: box.height }}>
      {lines.map((l, i) => (
        <div key={i} className="pdfTransMask" style={{ left: l.left, top: l.top, width: l.width, height: l.height }} />
      ))}
      <div ref={ref} className="pdfTransText" style={{ fontSize: baseSize }}>
        <span>{parts}</span>
      </div>
    </div>
  );
}

// A formula of the original inside the translated text: its box cropped from
// its page's bitmap (pageRaster basePixels), sized in em of the paragraph's
// font so it shrinks with the text, and set on the text's baseline. While
// that page holds no pixels (not drawn yet, or let go far from the view)
// the formula's extracted text stands in; the next paint of the page's
// whole bitmap (`basePaints`) draws it. The bare class is set here, in the
// layout phase before the paragraph measures its fit, not through state; the
// box's size rides in custom properties so that class can let it go.
function TransMath({ span, page, em, scale, basePaints }) {
  const ref = useRef(null);
  // A formula from another page (moved across a page join) redraws when that
  // page's bitmap is painted: it announces it on its box.
  const [elsewhere, setElsewhere] = useState(0);
  useEffect(() => {
    const own = ref.current?.closest(".pdfPageWrap");
    const wrap = ref.current?.closest(".pdfViewer")?.querySelector(`.pdfPageWrap[data-page="${page}"]`);
    if (!wrap || wrap === own) return undefined;
    const bump = () => setElsewhere((n) => n + 1);
    wrap.addEventListener("pdfbasepaint", bump);
    return () => wrap.removeEventListener("pdfbasepaint", bump);
  }, [page]);
  useLayoutEffect(() => {
    const el = ref.current;
    const canvas = el?.firstChild;
    if (!canvas) return;
    const wrap = el.closest(".pdfViewer")?.querySelector(`.pdfPageWrap[data-page="${page}"]`);
    const src = wrap && basePixels(wrap);
    const ctx = src && wrap.offsetWidth ? canvas.getContext("2d") : null;
    el.classList.toggle("bare", !ctx);
    if (!ctx) return;
    // Backing pixels per scale-1 unit: the bitmap covers the whole page box.
    const k = (src.width * scale) / wrap.offsetWidth;
    const sw = (span.x2 - span.x1) * k, sh = (span.y2 - span.y1) * k;
    canvas.width = Math.max(1, Math.round(sw));
    canvas.height = Math.max(1, Math.round(sh));
    ctx.drawImage(src, span.x1 * k, span.y1 * k, sw, sh, 0, 0, canvas.width, canvas.height);
  }, [span, page, scale, basePaints, elsewhere]);
  return (
    <span ref={ref} className="pdfTransMath" style={{
      "--w": `${(span.x2 - span.x1) / em}em`,
      "--h": `${(span.y2 - span.y1) / em}em`,
      "--shift": `${-(span.y2 - span.base) / em}em`,
    }}>
      <canvas aria-hidden="true" />
      <span className="pdfTransMathText">{span.str}</span>
    </span>
  );
}

// The working indicator over a paragraph whose translation has not arrived:
// its original lines stay readable under a faint accent wash — shimmering
// while the request is in flight, still while it is only queued.
export function TransPending({ lines, busy }) {
  return (
    <div className={"pdfTransPending" + (busy ? " busy" : "")}>
      {lines.map((l, i) => (
        <div key={i} className="pdfTransPendingLine" style={{ left: l.left, top: l.top, width: l.width, height: l.height }} />
      ))}
    </div>
  );
}
