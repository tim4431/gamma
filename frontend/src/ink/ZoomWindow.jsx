// The zoom window (docs/dev/handwriting.md "Zoom window"): a panel docked
// under the PDF viewer's scroller (pdf/PdfViewer.jsx) showing a strip of one
// PDF page two to four times as large as at 100 %, so small handwriting is
// written large. App's inkZoom is the window, {page, x, y, w, h, mag, line,
// x0} in page points (ink/zoomWindow.js), and the one source of truth: the
// page inside the panel is the whole page at `mag` css px per point, moved
// so the window is what shows, and nothing reads a scroll position back.
// Behind it the page's pixels for the window and a margin around it
// (pdf/pageRaster.js zoomRasterPlan; never the whole page, which at 4× would
// pass the canvas caps), in front of it the page's own ink layer
// (MarkupLayers with chrome off: the same surface, groups and marks, so what
// is written here joins the page's current group through the same onStroke,
// and storage, replay and sync see nothing new). A stroke reaching into the
// window's right fifth moves it on along the line (advance).
//
// zoom: the window; size: the page's {width, height} in points; page: the
// pdf.js page (null until it loads); marks: the page's marks (useMarks);
// height / onHeight(px): the panel's height in css px, grip and bar
// included (this browser's inkZoomHeight; a drag of the grip applies it
// once, at the release); onChange(update): an update of the window,
// `update(window) → next`; onClose(); annotationMode, isCancel: the
// viewer's, for the raster; dark: the viewer flips page colours.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { MarkupLayers } from "../markup/MarkupLayers";
import { armedClasses, usePageTools } from "../markup/PageTools";
import { covers, renderRegion, zoomRasterNeed, zoomRasterPlan } from "../pdf/pageRaster.js";
import { ChevronLeftIcon, ChevronRightIcon, CornerDownLeftIcon, MinusIcon, PlusIcon, XIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import { InkTooltips } from "./InkLayer";
import {
  ZOOM_HEIGHT_MAX, ZOOM_HEIGHT_MIN, ZOOM_LINE_MAX, ZOOM_LINE_MIN, ZOOM_LINE_STEP, ZOOM_MAGS, advance, newLine, nextLine, prevLine,
  setLine, setMag, sized,
} from "./zoomWindow.js";

const clampHeight = (px) => Math.round(Math.max(ZOOM_HEIGHT_MIN, Math.min(ZOOM_HEIGHT_MAX, px)));
// A grip drag's height, before it is applied, on what stands beside the
// panel, set on the elements themselves so nothing above it renders: the
// scroller (PdfViewer's .pdfViewer, which gives up the panel's height, in
// the form its render writes) and App's --ink-zoom-h on .viewerWrap (the
// viewer's bottom controls stand above the panel). The renders after the
// release write the same values.
function previewHeight(panel, px) {
  const scroller = panel?.parentElement?.querySelector(":scope > .pdfViewer");   // i18n-ignore: a selector
  if (scroller) scroller.style.height = `calc(100% - ${px}px)`;
  panel?.closest(".viewerWrap")?.style.setProperty("--ink-zoom-h", `${px}px`);
}
// A raster the window moved off waits this long for the move to rest
// before it is drawn again (a drag of the page's band moves it every frame).
const RASTER_REST_MS = 80;

export function ZoomWindow({ zoom, size, page, marks, height, onHeight, onChange, onClose, annotationMode, isCancel, dark = false }) {
  const tools = usePageTools();
  const panelRef = useRef(null);
  const viewRef = useRef(null);
  const pageRef = useRef(null);
  const live = useRef(null);
  live.current = { size, onChange };
  // Every change goes through the window as it is then, and on this page only.
  const update = (fn) => live.current.onChange((win) => (win && win.page === zoom.page ? fn(win, live.current.size) : win));

  // While the grip is dragged: the height the panel shows, not applied yet.
  const [dragHeight, setDragHeight] = useState(null);
  const shownHeight = dragHeight ?? height;
  const dragging = dragHeight != null;
  // The view's size in css px: the window's w and h follow it (sized),
  // after a grip drag, whose every move changes it, once, at the release.
  const [view, setView] = useState(null);
  useLayoutEffect(() => {
    const el = viewRef.current;
    if (!el) return undefined;
    const measure = () => setView((v) => (v && v.width === el.clientWidth && v.height === el.clientHeight ? v
      : { width: el.clientWidth, height: el.clientHeight }));
    measure();
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, []);
  useEffect(() => {
    if (!dragging && view?.width && view.height) update((win, s) => sized(win, view, s));
  }, [view, zoom.page, zoom.mag, dragging]); // eslint-disable-line react-hooks/exhaustive-deps

  // The raster: two canvases in one slot (pageRaster.js `pair`'s reason),
  // the hidden one drawn into and then shown, so the panel never shows a
  // half-painted region. Drawn again only when what the view shows leaves
  // what is drawn, at another magnification or on another page. That is
  // the window, or during a grip drag, which sizes the window only at the
  // release, the window as large as the view.
  const canvasA = useRef(null), canvasB = useRef(null);
  const raster = useRef({ front: 0, drawn: null });
  const shown = dragging && view?.width && view.height ? { ...zoom, w: view.width / zoom.mag, h: view.height / zoom.mag } : zoom;
  useEffect(() => {
    const r = raster.current, need = page ? zoomRasterNeed(shown, size) : null;
    // another page's pixels never stand under this one's ink
    if (r.drawn && r.drawn.page !== page) {
      for (const c of [canvasA.current, canvasB.current]) if (c) { c.style.display = "none"; c.width = 0; c.height = 0; }
      r.drawn = null;
    }
    if (!need) return undefined;
    const d = r.drawn;
    if (d && d.page === page && d.scale === zoom.mag && d.mode === annotationMode && covers(d.rect, need)) return undefined;
    let task = null, dead = false;
    const timer = setTimeout(() => {
      const canvases = [canvasA.current, canvasB.current];
      const plan = zoomRasterPlan(shown, size, window.devicePixelRatio || 1);
      const back = canvases[1 - r.front], front = canvases[r.front];
      if (!plan || !back) return;
      try {
        ({ task } = renderRegion(page, { scale: plan.scale, rect: plan.rect, width: plan.width, height: plan.height, annotationMode, canvas: back }));
      } catch (error) {
        console.warn("Zoom window raster:", error);
        return;
      }
      task.promise.then(() => {
        if (dead) return;
        Object.assign(back.style, { left: `${plan.rect.x}px`, top: `${plan.rect.y}px`, width: `${plan.rect.width}px`,
          height: `${plan.rect.height}px`, display: "block" });
        if (front) { front.style.display = "none"; front.width = 0; front.height = 0; }
        r.front = 1 - r.front;
        r.drawn = { page, scale: plan.scale, mode: annotationMode, rect: plan.rect };
      }, (error) => {
        if (dead) return;
        if (!isCancel?.(error)) console.warn("Zoom window raster:", error);
        back.width = 0; back.height = 0;
      });
    }, d && d.page === page ? RASTER_REST_MS : 0);
    return () => { dead = true; clearTimeout(timer); task?.cancel(); };
  }, [page, shown.x, shown.y, shown.w, shown.h, shown.mag, size.width, size.height, annotationMode]); // eslint-disable-line react-hooks/exhaustive-deps
  // The canvases give their pixels up with the panel.
  useEffect(() => () => {
    for (const c of [canvasA.current, canvasB.current]) if (c) { c.width = 0; c.height = 0; }
  }, []);

  // The grip: a drag of the top edge, or ↑ / ↓ by 10 px, resizes the panel.
  // A drag shows its height on the panel and beside it as it moves
  // (previewHeight) and hands it to onHeight once, as it ends, so the
  // preference and App, whose render reaches every page's layers, change
  // once a drag; each key press applies at once, except during a drag,
  // which owns the height until it ends.
  const grip = useRef(null);
  const gripDown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    grip.current = { id: e.pointerId, y: e.clientY, from: height, at: height };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const gripMove = (e) => {
    const g = grip.current;
    if (g?.id !== e.pointerId) return;
    const next = clampHeight(g.from + g.y - e.clientY);
    if (next === g.at) return;
    g.at = next;
    previewHeight(panelRef.current, next);
    setDragHeight(next);
  };
  const gripEnd = (e) => {
    const g = grip.current;
    if (g?.id !== e.pointerId) return;
    grip.current = null;
    setDragHeight(null);
    if (g.at !== g.from) onHeight(g.at);
  };

  // A stroke written here that reached the window's right fifth moves it on.
  const onZoomStroke = (box) => update((win, s) => advance(win, box, s));
  const mag = zoom.mag;
  const seg = (label, icon, click, title) => (
    <button type="button" className="ctlBtn inkSegBtn" onClick={click} title={title || label} aria-label={label}>
      {icon}<span>{label}</span>
    </button>
  );
  return (
    <div className={"inkZoomPanel" + (dark ? " pdfDark" : "") + armedClasses(tools)} role="region" aria-label={t("Zoom window")} data-guide="ink.zoom"
      ref={panelRef} data-page={zoom.page} data-mag={mag} style={{ height: shownHeight }}>
      <div className="inkZoomGrip" role="separator" aria-orientation="horizontal" aria-label={t("Zoom window height")}
        aria-valuenow={shownHeight} aria-valuemin={ZOOM_HEIGHT_MIN} aria-valuemax={ZOOM_HEIGHT_MAX} tabIndex={0}
        title={t("Drag to make the zoom window taller or shorter")}
        onPointerDown={gripDown} onPointerMove={gripMove} onPointerUp={gripEnd} onPointerCancel={gripEnd} onLostPointerCapture={gripEnd}
        onKeyDown={(e) => {
          if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
          e.preventDefault();
          if (!grip.current) onHeight(clampHeight(height + (e.key === "ArrowUp" ? 10 : -10)));
        }} />
      <InkTooltips className="inkZoomBar" role="toolbar" aria-label={t("Zoom window controls")}>
        {seg(t("Previous line"), <ChevronLeftIcon size={16} />, () => update(prevLine))}
        {seg(t("Next line"), <ChevronRightIcon size={16} />, () => update(nextLine))}
        {seg(t("New line"), <CornerDownLeftIcon size={16} />, () => update(newLine), t("New line · Enter"))}
        <span className="pdfInkSep" />
        <span className="inkZoomGroup" role="group" aria-label={t("Line height")}>
          <button type="button" className="ctlBtn" aria-label={t("Smaller line height")} title={t("Smaller line height")}
            disabled={zoom.line <= ZOOM_LINE_MIN} onClick={() => update((win) => setLine(win, win.line - ZOOM_LINE_STEP))}><MinusIcon size={16} /></button>
          <span className="inkZoomReadout">{t("{n} pt", { n: zoom.line })}</span>
          <button type="button" className="ctlBtn" aria-label={t("Larger line height")} title={t("Larger line height")}
            disabled={zoom.line >= ZOOM_LINE_MAX} onClick={() => update((win) => setLine(win, win.line + ZOOM_LINE_STEP))}><PlusIcon size={16} /></button>
        </span>
        <span className="pdfInkSep" />
        <span className="inkZoomGroup" role="group" aria-label={t("Magnification")}>
          {ZOOM_MAGS.map((m) => (
            <button key={m} type="button" className={"ctlBtn inkSegBtn" + (mag === m ? " modeActive" : "")} aria-pressed={mag === m}
              title={t("Magnify {n}×", { n: m })} onClick={() => update((win, s) => setMag(win, m, view, s))}>{t("{n}×", { n: m })}</button>
          ))}
        </span>
        <button type="button" className="ctlBtn inkZoomClose" aria-label={t("Close the zoom window")} title={t("Close the zoom window (Z)")}
          onClick={onClose}><XIcon size={16} /></button>
      </InkTooltips>
      {/* touch-action: none in every mode: a finger here never scrolls or zooms; the window moves by its bar */}
      <div className="inkZoomView" ref={viewRef}>
        <div className="inkZoomPage" ref={pageRef} style={{ width: size.width * mag, height: size.height * mag,
          transform: `translate(${-zoom.x * mag}px, ${-zoom.y * mag}px)` }}>
          <canvas ref={canvasA} className="inkZoomRaster" />
          <canvas ref={canvasB} className="inkZoomRaster" />
          <MarkupLayers surface={zoom.page} wrapRef={pageRef} width={size.width} height={size.height} marks={marks}
            chrome={false} onZoomStroke={onZoomStroke} />
        </div>
      </div>
    </div>
  );
}
