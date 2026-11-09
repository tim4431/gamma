// The translated view's overlay over a page (docs/dev/translation.md "The
// queue" and "Formulas"): the layer over a page's blocks (TransLayer), a
// landed or streaming paragraph (TransPara), the formulas inside it (TransMath)
// and the wash over a paragraph still waiting (TransPending). PdfPage in
// pdf/PdfViewer.jsx renders the layer from the page's entry
// (pdf/translateJob.js), pdf/transBlock.js says what each block shows and where,
// pdf/transFit.js how large its text is set, pdf/transPaint.js in what colours.
import React, { memo, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { SCALE_VAR } from "../shared/lib/viewerZoom.js";
import { basePixels } from "./pageRaster.js";
import { REF_SCALE, blockBoxes, blockLanding, blockSetting, blockShow, cropBox, linesReached } from "./transBlock.js";
import { langOfKey } from "./translateRecall.js";
import { bestRatio, bodyBlocks, fitOf, keepCap, keepFit, keepPaints, layerCap, needsFit, pageCap, paintsOf, streamRatio } from "./transFit.js";
import { pagePaints, sampleScale } from "./transPaint.js";
import { hasGlyph, paperFamily } from "./paperFace.js";
import { pageReading } from "./transSelect.js";
import { textParts } from "./translateReply.js";

// The blocks of a page's `entry`, laid out at REF_SCALE on a layer of the page's
// `width` by `height` in pt and carried to the viewer's zoom by one transform
// of the zoom layer's SCALE_VAR, as the text layer is (pdf/PdfViewer.jsx), so
// a zoom neither renders nor lays out the layer. Promoting the layer
// (will-change) would blur it: it would be rasterised at REF_SCALE.
// `working`: translation is still arriving for the page. `page` is the
// page's number, `spanOf` looks up a token another block owns, `basePaints`
// counts the page bitmap's paints.
//
// Its body paragraphs share the page's cap, `--cap` on the layer, which their
// font size reads. It is written here, after the paragraphs' own layout effects
// have measured, and only with no job working on the page and no paragraph of
// it in flight (transFit.js layerCap), so text that has landed does not creep as
// more lands, nor while one paragraph is asked for again: one settle, at the
// job's end. That cap is
// kept with the fit, by the entry's key, so a remount in the middle of a job
// over a settled page writes it again and nothing that had landed moves, and
// with no job a remount works it out from the kept ratios, measuring nothing.
//
// Its blocks' ground and ink are read once a page, at the first paint it sees
// (readPaints), for every block at once, kept with the fit, and go down to the
// paragraphs that have any as props: a class and custom properties, no layout.
//
// A paragraph that lands fades in: its final text is not what the entry held at
// the layer's last commit (`shown`). A mount has none, so a show, a scroll back
// or a page entering the look-ahead draws what is there and moves nothing. A
// body paragraph starts streaming at the ratio the page's landed ones take, so
// it does not step down to it in sight.
//
// The paragraphs are drawn in the page's reading order (transSelect.js
// pageReading), keyed by block index, so a drag across columns selects and
// copies them as they read, and each carries its block's index (`data-block`),
// which a selection in it maps back to (pdf/PdfViewer.jsx). `still`: nothing on
// the page is selected (a read-only page, a tool armed), as on the text layer.
// A paragraph a job could not translate has a mark beside its original, which
// opens the paragraph's menu (`onMenu(i, x, y)`, at a point in the viewport).
export function TransLayer({ entry, page, width, height, working, spanOf, basePaints, still = false, onMenu = null }) {
  const { paras, texts, left } = entry;
  const ref = useRef(null);
  const shown = useRef(null);
  useLayoutEffect(() => { shown.current = entry; });
  const order = useMemo(() => pageReading(paras).order, [paras]);
  // A formula became a picture, or a box hidden while its text landed has a
  // height again: the blocks not measured for their text measure.
  const [pictures, onPicture] = useReducer((n) => n + 1, 0);
  const [, onPaints] = useReducer((n) => n + 1, 0);
  const body = useMemo(() => bodyBlocks(paras, left), [paras, left?.size]); // eslint-disable-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    const { cap, settles } = layerCap(paras, entry, body, working);
    if (settles) keepCap(paras, entry.key, cap);
    if (cap === null) ref.current.style.removeProperty("--cap");
    else ref.current.style.setProperty("--cap", cap);
  }, [paras, entry, body, working, pictures]);
  useLayoutEffect(() => {
    if (paintsOf(paras)) return;
    const src = basePixels(ref.current.closest(".pdfPageWrap"));
    if (!src) return;
    const paints = readPaints(src, paras, width, height);
    keepPaints(paras, paints);
    if (paints.size) onPaints();
  }, [paras, width, height, basePaints]);
  const paints = paintsOf(paras);
  const lang = langOfKey(entry.key);
  const bodyFit = pageCap(paras, body, texts);
  return (
    <div ref={ref} className={"pdfTransLayer" + (still ? " still" : "")} lang={lang || undefined} style={{
      width: width * REF_SCALE, height: height * REF_SCALE, transform: `scale(calc(var(${SCALE_VAR}) / ${REF_SCALE}))`,
    }}>
      {order.map((i) => {
        const show = blockShow(entry, i, working);
        if (!show) return null;
        if (show.pending) return <TransPending key={i} block={paras[i]} index={i} busy={show.busy} />;
        if (show.failed) return <TransFailed key={i} block={paras[i]} index={i} onMenu={onMenu} />;
        return (
          <TransPara key={show.key} paras={paras} index={i} text={show.text} streaming={show.streaming}
            again={show.again} busy={show.busy}
            start={show.streaming && body.has(i) ? bodyFit : null} landing={blockLanding(shown.current, entry, i)}
            lang={lang} body={body.has(i)} paint={paints?.get(i) || null} page={page} spanOf={spanOf}
            basePaints={basePaints} pictures={pictures} onPicture={onPicture} />
        );
      })}
    </div>
  );
}

// The paints of a page of `width` by `height` pt, read from its bitmap `src`, at
// the scale transPaint.js sets (sampleScale). Unsmoothed, so a stem keeps its
// colour. One canvas for every page, kept in main memory (willReadFrequently):
// the cost is one copy of the page's bitmap off the GPU, so it is made once a
// page, of the whole page, not once a block. A read that fails paints nothing
// and is kept as a read: it throws in a layout effect otherwise, which takes the
// app down, and would be tried again at every paint.
const scratch = document.createElement("canvas");
function readPaints(src, paras, width, height) {
  try {
    const k = sampleScale(width, height, src.width / width);
    scratch.width = Math.ceil(width * k);
    scratch.height = Math.ceil(height * k);
    const ctx = scratch.getContext("2d", { willReadFrequently: true });
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(src, 0, 0, scratch.width, scratch.height);
    const image = ctx.getImageData(0, 0, scratch.width, scratch.height);
    return pagePaints(paras, image.data, image.width, image.height, k);
  } catch {
    return new Map();
  } finally {
    scratch.width = 0; scratch.height = 0;
  }
}

// One translated paragraph. Two independent layers inside the block box: masks
// that cover EXACTLY the original text lines (per-line rects, so a figure the
// paragraph wraps around is never painted over), and the translated text. All
// the masks of a page lie under all its text (app.css), so no block's mask
// cuts the line of the block before it. The page layout never reflows.
//
// `paint` is what the block sits on and is written in, when it is not the usual
// (transPaint.js). A tinted mask paints the ground under the page's treatment
// (`.tinted`, app.css), and coloured ink goes on the words only: the formulas
// are pictures that have had the treatment already, so no filter may sit on
// anything that holds them.
//
// The text is set at a ratio of the block's size, `--fit`, the largest that fits
// the box, kept per block and text (transFit.js). The layout effect measures only
// what is not kept: a landing paragraph searches, a streaming one takes one step
// down, a remount or a zoom measures nothing, and a formula that became a picture
// measures its block again. A viewer hidden by display:none has no box to measure
// in and a page held up by a citation stays mounted under it, so nothing is
// measured or kept then, and the box's return measures as a picture does. The
// text's own height says whether it fits, not scrollHeight, which counts a tall
// formula's drawn box where it hangs past its line. Nothing clips the text: what
// does not fit at the floor runs out of its box, in sight. Its tokens come back
// as the original's formulas (TransMath), citation markers and URLs.
//
// It is set in the paper's own font when that font draws every word of it
// (paperFace.js), which then carries the block's weight and slant itself, and
// in the overlay's serif otherwise. The fit is kept with the face it measured.
//
// While it streams, only the lines its text has reached are masked; the others
// stay the original's, washed as a waiting paragraph's are. Where its text ends
// is read after the fit, from the layout the fit has just made, once an update.
// `start`: the ratio it starts streaming at, the block's size without one.
// `landing` (blockLanding) is what it mounts with: its text fades in, from
// nothing, or a little for words that streamed there and are in sight already.
// A paragraph asked for again (Translate again) keeps every mask while its new
// text streams (`again`), and shimmers over its text until that starts (`busy`).
const TransPara = memo(function TransPara({ paras, index, text, streaming, again = false, busy = false, start, landing, lang, body, paint, page, spanOf, basePaints, pictures, onPicture }) {
  const block = paras[index];
  const { box, lines } = blockBoxes(block, REF_SCALE);
  const { lead, ragged, indent, italic } = blockSetting(block, lang);
  // Its tokens read as an export writes them (translateReply.js textParts).
  const read = textParts(text, { spans: block.spans, page, spanOf });
  const face = paperFamily(block, read.filter((part) => typeof part === "string"), hasGlyph);
  const ref = useRef(null); // the text container (the fit-measured element)
  // The ratio it mounts with. React leaves it at that, the effect writes others.
  const [startFit] = useState(() => fitOf(paras, index)?.ratio ?? start ?? 1);
  const [landed] = useState(landing);
  const [reached, setReached] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !text) return undefined;
    const kept = fitOf(paras, index);
    const bare = el.querySelectorAll(".bare").length;
    if (!needsFit(kept, text, bare, streaming, face)) return undefined;
    const box = el.parentElement;
    const room = box.clientHeight;
    if (!room) {
      const back = new ResizeObserver(() => { if (box.clientHeight) onPicture(); });
      back.observe(box);
      return () => back.disconnect();
    }
    const fits = (ratio) => {
      el.style.setProperty("--fit", ratio);
      return el.offsetHeight <= room + 1 && el.scrollWidth <= el.clientWidth + 1;
    };
    el.style.setProperty("--cap", 1); // what the block itself can take, under no cap
    const ratio = streaming ? streamRatio(fits, kept?.ratio ?? startFit) : bestRatio(fits);
    el.style.setProperty("--fit", ratio);
    el.style.removeProperty("--cap");
    keepFit(paras, index, { text, ratio, bare, rough: streaming, face });
    return undefined;
  }, [paras, index, text, streaming, pictures, startFit, onPicture, face]);
  useLayoutEffect(() => {
    if (!streaming) return;
    const words = ref.current.firstElementChild;
    const bottom = words.offsetTop + words.offsetHeight;
    setReached((n) => Math.max(n, linesReached(lines, bottom))); // a step down of the fit takes no mask off
  }, [text, streaming]); // eslint-disable-line react-hooks/exhaustive-deps
  const parts = read.map((part, k) => {
    if (typeof part !== "string") {
      return <TransMath key={k} span={part.span} page={part.page} em={block.size} basePaints={basePaints} onPicture={onPicture} />;
    }
    return paint?.ink ? <span key={k} className="pdfTransInk">{part}</span> : part;
  });
  const shape = [streaming && "streaming", landed && "landing", landed === "streamed" && "streamed", body && "bodyText", ragged && "ragged",
    block.centred && "centred", face ? "paperFace" : block.bold && "bold", !face && italic && "italic", block.overFigure && "overFigure",
    paint?.ink && "inked"];
  const masked = streaming && !again ? lines.slice(0, reached) : lines;
  return (
    <div className={["pdfTransPara", ...shape.filter(Boolean)].join(" ")} data-block={index} style={{
      left: box.left, top: box.top, width: box.width, height: box.height, "--ground": paint?.ground ?? undefined, "--ink": paint?.ink ?? undefined,
      "--paper-face": face ? `"${face}"` : undefined,
    }}>
      {masked.map((l, i) => (
        <div key={i} className={"pdfTransMask" + (paint?.ground ? " tinted" : "")} style={{ left: l.left, top: l.top, width: l.width, height: l.height }} />
      ))}
      {masked.length < lines.length ? <PendingLines lines={lines.slice(masked.length)} busy /> : null}
      {busy ? <PendingLines lines={lines} busy /> : null}
      <div ref={ref} className="pdfTransText" style={{
        "--size": `${block.size * REF_SCALE}px`, "--fit": startFit, lineHeight: lead, textIndent: indent,
      }}>
        <span>{parts}</span>
      </div>
    </div>
  );
});

// A formula of the original inside the translated text: its box cropped from
// its page's bitmap (pageRaster basePixels), sized in em of the paragraph's
// font so it shrinks with the text, and set on the text's baseline. While
// that page holds no pixels (not drawn yet, or let go far from the view)
// the formula's extracted text stands in; the next paint of the page's
// whole bitmap (`basePaints`) draws it, and says so (`onPicture`), since the
// paragraph was measured with the text. The bare class is set here, in the
// layout phase before the paragraph measures its fit, not through state; the
// box's size rides in custom properties so that class can let it go.
function TransMath({ span, page, em, basePaints, onPicture }) {
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
    const pageWidth = Number(wrap?.dataset.ptWidth);
    const ctx = src && pageWidth ? canvas.getContext("2d") : null;
    const wasBare = el.classList.contains("bare");
    el.classList.toggle("bare", !ctx);
    if (!ctx) return;
    // Backing pixels per pt: the bitmap covers the whole page.
    const k = src.width / pageWidth;
    const sw = (span.x2 - span.x1) * k, sh = (span.y2 - span.y1) * k;
    canvas.width = Math.max(1, Math.round(sw));
    canvas.height = Math.max(1, Math.round(sh));
    ctx.drawImage(src, span.x1 * k, span.y1 * k, sw, sh, 0, 0, canvas.width, canvas.height);
    if (wasBare) onPicture();
  }, [span, page, basePaints, elsewhere, onPicture]);
  const crop = cropBox(span, em);
  return (
    <span ref={ref} className="pdfTransMath" style={{
      "--w": `${crop.w}em`, "--h": `${crop.h}em`,
      "--top": `${crop.top}em`, "--bottom": `${crop.bottom}em`, "--shift": `${crop.shift}em`,
    }}>
      <canvas aria-hidden="true" />
      <span className="pdfTransMathText">{span.str}</span>
    </span>
  );
}

// The working indicator over a paragraph whose translation has not arrived:
// its original lines stay readable under a faint accent wash — shimmering
// while the request is in flight, still while it is only queued.
const TransPending = memo(function TransPending({ block, index, busy }) {
  const { box, lines } = blockBoxes(block, REF_SCALE);
  return (
    <div className="pdfTransPara" data-block={index} style={box}>
      <PendingLines lines={lines} busy={busy} />
    </div>
  );
});

// The mark beside a paragraph a job could not translate: a thin bar at its
// left edge, over its original, quiet enough to read past. It is a button (its
// press opens the paragraph's menu, where Translate this paragraph is), outside
// any text, so a selection or a pen's drag over the page never takes it in.
const TransFailed = memo(function TransFailed({ block, index, onMenu }) {
  const { box } = blockBoxes(block, REF_SCALE);
  const label = t("This paragraph could not be translated");
  return (
    <div className="pdfTransPara" data-block={index} style={box}>
      {onMenu ? (
        <button type="button" className="pdfTransFailed" aria-label={label} title={label} aria-haspopup="menu"
          onClick={(e) => {
            e.stopPropagation();
            const r = e.currentTarget.getBoundingClientRect();
            onMenu(index, r.left, r.bottom + 4);
          }} />
      ) : <span className="pdfTransFailed" aria-hidden="true" />}
    </div>
  );
});

function PendingLines({ lines, busy }) {
  return (
    <div className={"pdfTransPending" + (busy ? " busy" : "")}>
      {lines.map((l, i) => (
        <div key={i} className="pdfTransPendingLine" style={{ left: l.left, top: l.top, width: l.width, height: l.height }} />
      ))}
    </div>
  );
}
