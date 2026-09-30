// The notebook view (docs/dev/notebooks.md): a page's sheets in the
// viewer's place, as paper, one under the other — the background and
// pattern drawn from each sheet's paper — with the ink layer a PDF page has
// (ink/InkLayer.jsx, keyed by the sheet's id instead of a page number) and
// an "Add page" sheet after the last. The scale follows the viewer's zoom ("page-width"
// fits the widest sheet). App owns the tree, the tools, the stroke history
// and the commits; PaperMenu edits a sheet's paper.
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { InkLayer } from "../ink/InkLayer";
import { PlusIcon, XIcon } from "../shared/ui/Icons";
import { Segmented } from "../settings/SettingsKit";
import { t, T } from "../shared/i18n/i18n.js";
import { installPinchZoom } from "../shared/lib/pinchZoom.js";
import { clampZoom } from "../shared/model/zoom.js";
import {
  DEFAULT_PAPER, DOT_RADIUS, LINE_WIDTH, PAPER_COLORS, PAPER_SIZES, isLandscape, paperLines, paperSizeKey, turnPaper,
} from "./notebook";
import "./notebook.css";

const PAD = 24; // css px around the column at fit-width
const EMPTY = [];

// A point of a notebook, in the only terms a zoom scales cleanly: which sheet
// and where on it (as a fraction of its box). Neither the column's padding nor
// the gaps between sheets scale with the zoom, so a hold kept as a ratio of
// scroll offsets drifts by them; a sheet's own box does scale, exactly.
// `cx`/`cy` are content coordinates (what `scrollLeft` + a view offset gives).
// Off the sheets — in a gap, or past the last one — the nearest sheet's
// fraction, which runs outside 0..1 and reads back the same way.
function holdAt(el, cx, cy) {
  let best = null, dist = Infinity;
  for (const node of el.querySelectorAll(".nbSheet")) {
    const top = node.offsetTop, bottom = top + node.offsetHeight;
    const d = cy < top ? top - cy : cy > bottom ? cy - bottom : 0;
    if (d < dist) { dist = d; best = node; }
  }
  if (!best || !best.offsetWidth || !best.offsetHeight) return null;
  return {
    id: best.dataset.sheetId,
    fx: (cx - best.offsetLeft) / best.offsetWidth,
    fy: (cy - best.offsetTop) / best.offsetHeight,
  };
}

// Scroll so a hold's point sits at (hold.vx, hold.vy) in the view. Call it
// once the sheets have taken their new size; false if the sheet it names is
// gone, and the caller should fall back.
function applyHold(el, hold) {
  const node = [...el.querySelectorAll(".nbSheet")].find((n) => n.dataset.sheetId === hold.id);
  if (!node) return false;
  el.scrollLeft = node.offsetLeft + hold.fx * node.offsetWidth - hold.vx;
  el.scrollTop = node.offsetTop + hold.fy * node.offsetHeight - hold.vy;
  return true;
}

// A sheet's paper: the background, then the pattern as two paths (lines,
// and dots drawn as round-capped zero-length segments).
export function PaperBackground({ paper }) {
  const { lines, dots } = useMemo(() => {
    const geo = paperLines(paper);
    return {
      lines: geo.lines.map(([x1, y1, x2, y2]) => `M${x1} ${y1}L${x2} ${y2}`).join(""),
      dots: geo.dots.map(([x, y]) => `M${x} ${y}h0`).join(""),
    };
  }, [paper]);
  return (
    <svg className="nbPaper" viewBox={`0 0 ${paper.width} ${paper.height}`} preserveAspectRatio="none" aria-hidden="true">
      <rect width={paper.width} height={paper.height} fill={paper.color} />
      {lines ? <path d={lines} fill="none" stroke={paper.line} strokeWidth={LINE_WIDTH} /> : null}
      {dots ? <path d={dots} fill="none" stroke={paper.line} strokeWidth={DOT_RADIUS * 2} strokeLinecap="round" /> : null}
    </svg>
  );
}

const NotebookSheet = React.memo(function NotebookSheet({ sheet, number, scale, blocks, ink, selection, flash }) {
  const wrapRef = useRef(null);
  const { paper } = sheet;
  return (
    <div ref={wrapRef} className="nbSheet" data-sheet-id={sheet.id}
      style={{ width: `${paper.width * scale}px`, height: `${paper.height * scale}px` }}
      aria-label={t("Page {n}", { n: number })}>
      <PaperBackground paper={paper} />
      {blocks.length || ink.onStroke ? (
        <InkLayer pageNumber={sheet.id} wrapRef={wrapRef} width={paper.width} height={paper.height}
          blocks={blocks} tool={ink.onStroke ? ink.tool : null} penTool={ink.onStroke ? ink.penTool : null}
          penOnly={ink.penOnly} pressure={ink.pressure} eraserMode={ink.eraserMode} eraserSize={ink.eraserSize}
          lassoMode={ink.lassoMode} selection={selection} flash={flash}
          onStroke={ink.onStroke} onErase={ink.onErase} onErasePartial={ink.onErasePartial}
          onSelect={ink.onSelect} onAction={ink.onAction} onMoveSelection={ink.onMoveSelection} onJump={ink.onJump} />
      ) : null}
      <span className="nbSheetNo" aria-hidden="true">{number}</span>
    </div>
  );
});

// sheets: notebook.js sheetsOf; inkBySheet: sheet id → its ink blocks;
// scaleValue: "page-width" or a number (string); scrollRef.current(sheetId,
// box?) scrolls a sheet (and a box on it, in points) into view;
// onCurrentSheet(id) reports the sheet under the middle of the view.
export function NotebookViewer({ sheets, inkBySheet, scaleValue = "page-width", onEffectiveScale, onZoomTo, readOnly,
  onAddSheet, onCurrentSheet, scrollRef, inkTool = null, inkPenTool = null, inkPenOnly = true, inkPressure = true,
  inkEraserMode = "stroke", inkEraserSize = 1, inkLassoMode = "free", inkSelection = null, inkFlash = null,
  onInkStroke, onInkErase, onInkErasePartial, onInkSelect, onInkAction, onInkMoveSelection, onInkJump }) {
  const boxRef = useRef(null);
  const [boxW, setBoxW] = useState(0);
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const measure = () => setBoxW(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const widest = sheets.reduce((w, s) => Math.max(w, s.paper.width), sheets.length ? 0 : DEFAULT_PAPER.width);
  const fit = boxW ? Math.max(0.1, (boxW - 2 * PAD) / widest) : 1;
  const scale = scaleValue === "page-width" || !Number(scaleValue) ? fit : Number(scaleValue);

  // A zoom keeps the point at the top of the view where it was, unless it
  // named a point to hold instead (holdRef, which a pinch fills in with what
  // its fingers were on). The hold is read before the re-render and spent
  // here, after the sheets have taken their new size.
  const prevScale = useRef(scale);
  const holdRef = useRef(null); // { id, fx, fy, vx, vy } from holdAt, plus the view point to put it at
  useLayoutEffect(() => {
    const el = boxRef.current, was = prevScale.current;
    prevScale.current = scale;
    const hold = holdRef.current;
    holdRef.current = null;
    if (el && was && was !== scale && !(hold && applyHold(el, hold))) {
      el.scrollTop = el.scrollTop * (scale / was);
    }
    onEffectiveScale?.(scale);
  }, [scale]); // eslint-disable-line react-hooks/exhaustive-deps

  // Ctrl + wheel (and a trackpad pinch, which arrives as one) zooms.
  const zoomRef = useRef(null);
  zoomRef.current = { scale, onZoomTo };
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      if (!e.ctrlKey || !zoomRef.current.onZoomTo) return;
      e.preventDefault();
      zoomRef.current.onZoomTo(zoomRef.current.scale * Math.exp(-e.deltaY / 300));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Two fingers zoom and pan the sheets, the only zoom gesture a tablet has
  // (the viewport meta turns the browser's own off — docs/dev/ipad.md). The
  // gesture itself is shared/lib/pinchZoom.js, the same reader the PDF viewer
  // uses; only the commit below is the notebook's own.
  const columnRef = useRef(null);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    return installPinchZoom(el, {
      layer: () => columnRef.current,
      scale: () => zoomRef.current.scale,
      clamp: clampZoom,
      onCommit: (next, g) => {
        // A zoom re-lays-out the sheets, so the paper the fingers started over
        // is handed to the hold, which puts it back under them afterwards —
        // that carries the pan too, since it aims at the final midpoint. A
        // change this small would be rounded away by the commit, leaving the
        // hold unclaimed; below it the fingers only dragged, so just pan.
        const hold = Math.abs(next - g.scale) > 1e-3 ? holdAt(el, g.sl + g.mx, g.st + g.my) : null;
        if (hold) {
          holdRef.current = { ...hold, vx: g.vx, vy: g.vy };
          zoomRef.current.onZoomTo?.(next);
        } else {
          holdRef.current = null;
          el.scrollLeft = g.sl + g.mx - g.vx;
          el.scrollTop = g.st + g.my - g.vy;
        }
      },
    });
  }, []);

  // The sheet under the middle of the view, for the paper menu.
  const currentRef = useRef("");
  const sheetIds = sheets.map((s) => s.id).join(",");
  useEffect(() => {
    const el = boxRef.current;
    if (!el || !onCurrentSheet) return undefined;
    const pick = () => {
      const mid = el.getBoundingClientRect().top + el.clientHeight / 2;
      let best = "", dist = Infinity;
      for (const node of el.querySelectorAll(".nbSheet")) {
        const r = node.getBoundingClientRect();
        const d = mid < r.top ? r.top - mid : mid > r.bottom ? mid - r.bottom : 0;
        if (d < dist) { dist = d; best = node.dataset.sheetId; }
      }
      if (best !== currentRef.current) { currentRef.current = best; onCurrentSheet(best); }
    };
    pick();
    el.addEventListener("scroll", pick, { passive: true });
    return () => el.removeEventListener("scroll", pick);
  }, [sheetIds, scale, onCurrentSheet]);

  useEffect(() => {
    if (!scrollRef) return undefined;
    scrollRef.current = (sheetId, box = null) => {
      const el = boxRef.current;
      const node = el && [...el.querySelectorAll(".nbSheet")].find((n) => n.dataset.sheetId === sheetId);
      if (!node) return false;
      const k = prevScale.current;
      const y = node.offsetTop + (box ? box[1] * k : 0) - (box ? 120 : 12);
      el.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
      return true;
    };
    return () => { scrollRef.current = null; };
  }, [scrollRef]);

  // Stable handlers, so a memoized sheet re-renders only for its own ink.
  const cbRef = useRef(null);
  cbRef.current = { onInkStroke, onInkErase, onInkErasePartial, onInkSelect, onInkAction, onInkMoveSelection, onInkJump };
  const stable = useMemo(() => {
    const out = {};
    for (const k of Object.keys(cbRef.current)) out[k] = (...a) => cbRef.current[k]?.(...a);
    return out;
  }, []);
  const ink = useMemo(() => ({
    tool: inkTool, penTool: inkPenTool, penOnly: inkPenOnly, pressure: inkPressure, eraserMode: inkEraserMode,
    eraserSize: inkEraserSize, lassoMode: inkLassoMode,
    onStroke: onInkStroke ? stable.onInkStroke : undefined, onErase: onInkErase ? stable.onInkErase : undefined,
    onErasePartial: onInkErasePartial ? stable.onInkErasePartial : undefined, onSelect: onInkSelect ? stable.onInkSelect : undefined,
    onAction: onInkAction ? stable.onInkAction : undefined, onMoveSelection: onInkMoveSelection ? stable.onInkMoveSelection : undefined,
    onJump: onInkJump ? stable.onInkJump : undefined,
  }), [inkTool, inkPenTool, inkPenOnly, inkPressure, inkEraserMode, inkEraserSize, inkLassoMode, stable,
    !onInkStroke, !onInkErase, !onInkErasePartial, !onInkSelect, !onInkAction, !onInkMoveSelection, !onInkJump]); // eslint-disable-line react-hooks/exhaustive-deps

  const lastWidth = (sheets.at(-1)?.paper.width || DEFAULT_PAPER.width) * scale;
  return (
    <div ref={boxRef} className={"nbViewer" + (inkTool ? " inkArmed" : "") + (inkTool && !inkPenOnly ? " inkTouchDraw" : "")}
>
      <div ref={columnRef} className="nbColumn" style={{ padding: `${PAD}px` }}>
        {sheets.map((s, i) => (
          <NotebookSheet key={s.id} sheet={s} number={i + 1} scale={scale} blocks={inkBySheet.get(s.id) || EMPTY} ink={ink}
            selection={inkSelection && inkSelection.page === s.id ? inkSelection : null}
            flash={inkFlash && (inkBySheet.get(s.id) || EMPTY).some((b) => b.id === inkFlash.id) ? inkFlash : null} />
        ))}
        {!readOnly && onAddSheet ? (
          <button type="button" className="nbAddSheet" style={{ width: `${lastWidth}px` }} onClick={onAddSheet}
            title={t("Add a page at the end")}>
            <PlusIcon size={16} /> {t("Add page")}
          </button>
        ) : null}
      </div>
    </div>
  );
}

const PATTERN_CHOICES = [["blank", T("Blank")], ["ruled", T("Ruled")], ["grid", T("Grid")], ["dots", T("Dots")]];
const SPACING_CHOICES = [[18, T("Narrow"), null, T("Lines 18 pt apart")], [24, T("Medium"), null, T("Lines 24 pt apart")],
  [32, T("Wide"), null, T("Lines 32 pt apart")]];
const SIZE_LABELS = { a4: "A4", letter: T("Letter"), a5: "A5" };
const COLOR_NAMES = { "#ffffff": T("White"), "#fbf7ec": T("Cream"), "#f2f3f5": T("Gray"), "#2b2d31": T("Dark") };
// Pattern lines that read on each background.
const LINE_FOR = { "#2b2d31": "#51555c" };

// The paper of one sheet: size and orientation, pattern and spacing,
// background. Every choice applies to that sheet at once (a new page takes
// the paper of the page before it); "Apply to all pages" gives every sheet
// of the page this paper.
export function PaperMenu({ paper, number, onChange, onApplyAll, onClose }) {
  const size = paperSizeKey(paper);
  const set = (patch) => onChange({ ...paper, ...patch });
  const setSize = (key) => {
    const s = PAPER_SIZES.find((x) => x.key === key);
    const next = { ...paper, width: s.width, height: s.height };
    onChange(isLandscape(paper) ? turnPaper(next) : next);
  };
  return (
    <div className="nbPaperMenu" role="dialog" data-guide="notebook.paperMenu" aria-label={t("Paper of page {n}", { n: number })}>
      <div className="nbPaperHead">
        <span>{t("Paper · page {n}", { n: number })}</span>
        <button type="button" className="uiClose" onClick={onClose} title={t("Close")} aria-label={t("Close")}><XIcon size={14} /></button>
      </div>
      <div className="nbPaperRow">
        <span className="nbPaperLabel">{t("Size")}</span>
        <Segmented value={size} onChange={setSize}
          options={PAPER_SIZES.map((s) => [s.key, SIZE_LABELS[s.key], null, `${Math.round(s.width)} × ${Math.round(s.height)} pt`])} />
      </div>
      <div className="nbPaperRow">
        <span className="nbPaperLabel">{t("Orientation")}</span>
        <Segmented value={isLandscape(paper) ? "landscape" : "portrait"}
          onChange={(v) => { if ((v === "landscape") !== isLandscape(paper)) onChange(turnPaper(paper)); }}
          options={[["portrait", T("Portrait")], ["landscape", T("Landscape")]]} />
      </div>
      <div className="nbPaperRow">
        <span className="nbPaperLabel">{t("Pattern")}</span>
        <Segmented value={paper.pattern} onChange={(pattern) => set({ pattern })} options={PATTERN_CHOICES} />
      </div>
      {paper.pattern !== "blank" ? (
        <div className="nbPaperRow">
          <span className="nbPaperLabel">{t("Spacing")}</span>
          <Segmented value={paper.spacing} onChange={(spacing) => set({ spacing })} options={SPACING_CHOICES} />
        </div>
      ) : null}
      <div className="nbPaperRow">
        <span className="nbPaperLabel">{t("Background")}</span>
        <span className="nbPaperColors">
          {PAPER_COLORS.map((c) => (
            <button key={c} type="button" className={`colorBtn nbPaperSwatch ${paper.color === c ? "selected" : ""}`}
              style={{ background: c }} title={t(COLOR_NAMES[c])} aria-label={t(COLOR_NAMES[c])} aria-pressed={paper.color === c}
              onClick={() => set({ color: c, line: LINE_FOR[c] || DEFAULT_PAPER.line })} />
          ))}
        </span>
      </div>
      <div className="nbPaperActions">
        <button type="button" className="uiBtn sm" onClick={onApplyAll} title={t("Give every page here this paper")}>
          {t("Apply to all pages")}
        </button>
      </div>
    </div>
  );
}
