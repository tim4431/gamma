// The notebook view (docs/dev/notebooks.md): a page's sheets in the
// viewer's place, as paper, one under the other — the background and
// pattern drawn from each sheet's paper — with the layers a PDF page has
// (markup/MarkupLayers.jsx, keyed by the sheet's id instead of a page number) and
// an "Add page" sheet after the last. The scale follows the viewer's zoom ("page-width"
// fits the widest sheet). App owns the tree, the tools, the stroke history
// and the commits; PaperMenu edits a sheet's paper.
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { MarkupLayers, NO_MARKS } from "../markup/MarkupLayers";
import { armedClasses, usePageTools } from "../markup/PageTools";
import { PlusIcon, XIcon } from "../shared/ui/Icons";
import { Segmented } from "../settings/SettingsKit";
import { t, T } from "../shared/i18n/i18n.js";
import { installViewerZoom } from "../shared/lib/viewerZoom.js";
import { clampZoom } from "../shared/model/zoom.js";
import {
  DEFAULT_PAPER, DOT_RADIUS, LINE_WIDTH, PAPER_COLORS, PAPER_SIZES, isLandscape, paperLines, paperSizeKey, turnPaper,
} from "./notebook";
import "./notebook.css";

const PAD = 24; // css px around the column at fit-width

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

const NotebookSheet = React.memo(function NotebookSheet({ sheet, number, scale, marks }) {
  const wrapRef = useRef(null);
  const { paper } = sheet;
  return (
    <div ref={wrapRef} className="nbSheet" data-sheet-id={sheet.id}
      style={{ width: `${paper.width * scale}px`, height: `${paper.height * scale}px` }}
      aria-label={t("Page {n}", { n: number })}>
      <PaperBackground paper={paper} />
      <MarkupLayers surface={sheet.id} wrapRef={wrapRef} width={paper.width} height={paper.height} marks={marks} />
      <span className="nbSheetNo" aria-hidden="true">{number}</span>
    </div>
  );
});

// sheets: notebook.js sheetsOf; marks: sheet id → what its layers draw
// (markup/MarkupLayers.jsx useMarks); scaleValue: "page-width" or a number
// (string); scrollRef.current(sheetId, box?) scrolls a sheet (and a box on
// it, in points) into view; onCurrentSheet(id) reports the sheet under the
// middle of the view.
export function NotebookViewer({ sheets, marks, scaleValue = "page-width", onEffectiveScale, onZoomTo, readOnly,
  onAddSheet, onCurrentSheet, scrollRef }) {
  const tools = usePageTools();
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
  // named a point to hold instead (holdRef, which the gestures below fill in
  // with the paper under the cursor or the fingers). The hold is read before
  // the re-render and spent here, once the sheets have their new size.
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
    // A zoom from elsewhere (the buttons, fit-width, a resize) is what the
    // next gesture compounds on.
    viewerZoomRef.current?.sync(scale);
    onEffectiveScale?.(scale);
  }, [scale]); // eslint-disable-line react-hooks/exhaustive-deps

  // Ctrl/⌘ + wheel and two fingers (pinch to zoom, drag to pan) are read by
  // shared/lib/viewerZoom.js, the same reader the PDF viewer uses; what is
  // here is the commit, which holds a point of the paper still.
  const onZoomToRef = useRef(null);
  onZoomToRef.current = onZoomTo;
  const columnRef = useRef(null);
  const viewerZoomRef = useRef(null);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const zoom = installViewerZoom(el, {
      layer: () => columnRef.current,
      clamp: clampZoom,
      // The paper under the cursor stays under it.
      onWheelZoom: (next, at) => {
        const hold = holdAt(el, el.scrollLeft + at.x, el.scrollTop + at.y);
        if (hold) holdRef.current = { ...hold, vx: at.x, vy: at.y };
        onZoomToRef.current?.(next);
      },
      onPinchZoom: (next, g) => {
        // A zoom re-lays-out the sheets, so the paper the fingers started over
        // is handed to the hold, which puts it back under them afterwards —
        // that carries the pan too, since it aims at the final midpoint. A
        // change this small would be rounded away by the commit, leaving the
        // hold unclaimed; below it the fingers only dragged, so just pan.
        const hold = Math.abs(next - g.scale) > 1e-3 ? holdAt(el, g.sl + g.mx, g.st + g.my) : null;
        if (hold) {
          holdRef.current = { ...hold, vx: g.vx, vy: g.vy };
          onZoomToRef.current?.(next);
        } else {
          holdRef.current = null;
          el.scrollLeft = g.sl + g.mx - g.vx;
          el.scrollTop = g.st + g.my - g.vy;
        }
      },
    });
    viewerZoomRef.current = zoom;
    // The scale it mounted at: the effect that syncs later ones is a layout
    // effect, so it has already run and found no gesture to tell.
    zoom.sync(prevScale.current);
    return () => { viewerZoomRef.current = null; zoom.dispose(); };
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

  const lastWidth = (sheets.at(-1)?.paper.width || DEFAULT_PAPER.width) * scale;
  return (
    <div ref={boxRef} className={"nbViewer" + armedClasses(tools)}>
      <div ref={columnRef} className="nbColumn" style={{ padding: `${PAD}px` }}>
        {sheets.map((s, i) => (
          <NotebookSheet key={s.id} sheet={s} number={i + 1} scale={scale} marks={marks.get(s.id) || NO_MARKS} />
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
