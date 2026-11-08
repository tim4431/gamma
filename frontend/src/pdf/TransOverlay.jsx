// The translated view's overlay over a page (docs/dev/translation.md "The
// queue" and "Formulas"): the layer over the page (TransLayer), a landed or
// streaming paragraph (TransPara), the formulas inside it (TransMath) and
// the wash over a paragraph still waiting (TransPending). PdfPage in
// pdf/PdfViewer.jsx draws the layer from the page's entry
// (pdf/translateJob.js).
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { SCALE_VAR } from "../shared/lib/viewerZoom.js";
import { basePixels } from "./pageRaster.js";
import { textParts } from "./translatePlan.js";

// A page's translated view: masks and refills sit between the canvas and the
// text layer, so selecting the (invisible) original text still paints its
// selection highlight on top of the overlay. pointer-events: none —
// highlighting, links and search always act on the original.
//
// Laid out at `at`, a zoom the page box was at (PdfPage: the settled one,
// while the page is inside the look-ahead), and stretched to the box as it
// is by the ratio of the layer's zoom to that: a wheel train renders none
// of it, and the paragraphs fit their boxes again once, when it stops. Sized
// to the page at `at`, never inset 0, so the stretch ends at the page box
// and cannot widen the scroll area. At rest the ratio is exactly 1.
// entry: the page's translation (pdf/translateJob.js); working: more of it
// on its way; page: its number; baseW × baseH: the page at scale 1.
export function TransLayer({ entry, working, page, baseW, baseH, at, spanOf, basePaints }) {
  return (
    <div className="pdfTransLayer" style={{
      width: baseW ? baseW * at : undefined, height: baseH ? baseH * at : undefined,
      transform: `scale(calc(var(${SCALE_VAR}) / ${at}))`,
    }}>
      {entry.paras.map((p, i) => {
        // Results by block index: text, null (came back untranslated: the
        // original shows), "" (its words went to the part before it:
        // masked, nothing written), or missing (not landed yet).
        if (!p.translate) return null;
        const final = entry.texts[i];
        const partial = final ? null : entry.partial?.[i];
        const text = final === "" ? "" : final || partial;
        const pad = 1.5;
        const lns = p.lines || [];
        const box = {
          left: p.x1 * at - pad, top: p.y1 * at - pad,
          width: (p.x2 - p.x1) * at + 2 * pad, height: (p.y2 - p.y1) * at + 2 * pad,
        };
        const lines = lns.map((l, j) => {
          // Fill the inter-line leading too: the original's line pitch
          // exceeds the glyph-box height, and slivers of the original text
          // otherwise peek through between masks.
          const next = lns[j + 1];
          const bottom = next && next.y1 - l.y2 < p.size * 1.2 ? next.y1 : l.y2;
          return {
            left: (l.x1 - p.x1) * at, top: (l.y1 - p.y1) * at,
            width: (l.x2 - l.x1) * at + 2 * pad,
            height: (bottom - l.y1) * at + 2 * pad,
          };
        });
        if (text !== "" && !text) {
          // Nothing to show yet: the original stays visible, washed while
          // its translation is queued or in flight (not once this job could
          // not translate it).
          if (final === undefined && working && (entry.busy?.has(i)
              || (entry.queued && !entry.failed?.has(i)))) {
            return (
              <div key={i} className="pdfTransPara" style={box}>
                <TransPending lines={lines} busy={entry.busy?.has(i)} />
              </div>
            );
          }
          return null;
        }
        return (
          // Keyed by its final text too: a translation asked again (now
          // with the paper's brief) fades in like a new one.
          <TransPara key={final ? `${i}:${final}` : i} box={box} lines={lines} baseSize={p.size * at} text={text}
            streaming={!final && final !== ""} block={p} page={page}
            spanOf={spanOf} basePaints={basePaints} />
        );
      })}
    </div>
  );
}

// One translated paragraph. Two independent layers inside the block box:
// masks that cover EXACTLY the original text lines (per-line rects, so a
// figure the paragraph wraps around is never painted over), and the
// translated text with an inline background behind each rendered line
// (box-decoration-break: clone) — so text and mask stay readable even where
// the browser's line breaks don't coincide with the original's. The text is
// drawn at the original font size, shrunk in steps until it fits the box —
// the page layout never reflows. The measured shrink loop runs a handful of
// synchronous reflows per block; pages have tens of blocks, which is fine.

// A translated paragraph over its original lines: masks over exactly those
// lines, the translation in them, shrunk until it fits. Its tokens come back
// as the original's formulas (TransMath), citation markers and URLs.
export function TransPara({ box, lines, baseSize, text, streaming, block, page, spanOf, basePaints }) {
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
  // Its tokens read as an export writes them (translatePlan.js textParts).
  const parts = textParts(text, { spans: block?.spans, page, spanOf }).map((part, k) => (typeof part === "string" ? part
    : <TransMath key={k} span={part.span} page={part.page} em={block.size} basePaints={basePaints} />));
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
// box's size rides in custom properties so that class can let it go. The
// crop needs no zoom: the bitmap covers the page box, whose size at scale 1
// the box carries (data-base-w), so it is right mid-zoom too, and redrawn
// only with new pixels.
function TransMath({ span, page, em, basePaints }) {
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
    const baseW = Number(wrap?.dataset.baseW);
    const ctx = src && baseW > 0 ? canvas.getContext("2d") : null;
    el.classList.toggle("bare", !ctx);
    if (!ctx) return;
    // Backing pixels per scale-1 unit: the bitmap covers the whole page box.
    const k = src.width / baseW;
    const sw = (span.x2 - span.x1) * k, sh = (span.y2 - span.y1) * k;
    canvas.width = Math.max(1, Math.round(sw));
    canvas.height = Math.max(1, Math.round(sh));
    ctx.drawImage(src, span.x1 * k, span.y1 * k, sw, sh, 0, 0, canvas.width, canvas.height);
  }, [span, page, basePaints, elsewhere]);
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
