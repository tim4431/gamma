import React, { useEffect, useRef } from "react";
import { InkLayer } from "../ink/InkLayer.jsx";
import { MenuSelect } from "../shared/ui/Menus.jsx";
import { t, T } from "../shared/i18n/i18n.js";
import "./notebooks.css";

export const DEFAULT_PAPER = { width: 595.28, height: 841.89, color: "#ffffff", pattern: "blank", spacing: 24, line_color: "#d6dce5" };
const patterns = [["blank", T("Blank")], ["ruled", T("Ruled")], ["grid", T("Grid")], ["dots", T("Dots")]];

function Sheet({ sheet, index, inkBlocks, readOnly, onPaper, inkProps }) {
  const wrap = useRef(null);
  const paper = { ...DEFAULT_PAPER, ...sheet.properties.paper };
  const s = paper.spacing;
  const pattern = paper.pattern;
  // Paper and ink share point coordinates, independent of display size.
  return <section className="notebookSheet" data-sheet-id={sheet.id}>
    <div className="notebookSheetBar">
      <span>{t("Page {n}", { n: index + 1 })}</span>
      {!readOnly && <>
        <MenuSelect label={t("Paper pattern")} value={pattern} options={patterns.map(([value, label]) => [value, t(label)])}
          onChange={(value) => onPaper(sheet.id, { ...paper, pattern: value })} />
        <label>{t("Paper color")} <input type="color" aria-label={t("Paper color")} value={paper.color}
          onChange={(e) => onPaper(sheet.id, { ...paper, color: e.target.value })} /></label>
        <label>{t("Line color")} <input type="color" aria-label={t("Line color")} value={paper.line_color}
          onChange={(e) => onPaper(sheet.id, { ...paper, line_color: e.target.value })} /></label>
        <label>{t("Spacing")} <input type="number" aria-label={t("Paper spacing")} min="4" max="200" value={s}
          onChange={(e) => { const v = Number(e.target.value); if (v >= 4 && v <= 200) onPaper(sheet.id, { ...paper, spacing: v }); }} /></label>
      </>}
    </div>
    <div ref={wrap} className="notebookPaper" style={{ aspectRatio: `${paper.width}/${paper.height}`, maxWidth: paper.width, background: paper.color }}>
      <svg className="notebookRuling" viewBox={`0 0 ${paper.width} ${paper.height}`} aria-hidden="true">
        <defs><pattern id={`paper-${sheet.id}`} width={s} height={s} patternUnits="userSpaceOnUse">
          {pattern === "dots" ? <circle cx="0" cy="0" r="0.65" fill={paper.line_color} /> :
            <path d={pattern === "grid" ? `M ${s} 0 H 0 V ${s}` : `M 0 ${s} H ${s}`} fill="none" stroke={paper.line_color} strokeWidth="0.5" />}
        </pattern></defs>
        {pattern !== "blank" && <rect x={pattern === "dots" ? s / 2 : 0} y={pattern === "dots" ? s / 2 : 0}
          width={paper.width - (pattern === "dots" ? s / 2 : 0)} height={paper.height - (pattern === "dots" ? s / 2 : 0)} fill={`url(#paper-${sheet.id})`} />}
      </svg>
      <InkLayer {...inkProps} pageNumber={sheet.id} wrapRef={wrap} width={paper.width} height={paper.height}
        blocks={inkBlocks.filter((b) => b.properties.sheet_id === sheet.id)} />
    </div>
  </section>;
}

export default function NotebookViewer({ sheets, inkBlocks, readOnly, canChangeDefaults, onAppend, onPaper, onDefaultPaper, defaultPaper,
  onOpenTools, onExport, scrollRef, inkProps }) {
  const scroller = useRef(null);
  useEffect(() => {
    const jump = ({ position }) => {
      const sheetId = position?.sheet_id;
      if (!sheetId) return;
      const node = [...(scroller.current?.querySelectorAll("[data-sheet-id]") || [])].find((el) => el.dataset.sheetId === sheetId);
      node?.scrollIntoView({ block: "start", behavior: "smooth" });
    };
    if (scrollRef) scrollRef.current = jump;
    return () => { if (scrollRef?.current === jump) scrollRef.current = null; };
  }, [scrollRef]);
  return <div className="notebookViewer" ref={scroller}>
    {!readOnly && <div className="notebookControls">
      <button className="uiBtn" onClick={onOpenTools}>{t("Handwriting tools")}</button>
      {canChangeDefaults && <MenuSelect label={t("New page size")} value={`${defaultPaper.width}x${defaultPaper.height}`}
        options={[["612x792", t("Letter")], ["595.28x841.89", t("A4")], ["595.28x419.53", t("A5 landscape")]]}
        onChange={(value) => { const [width, height] = value.split("x").map(Number); onDefaultPaper({ ...defaultPaper, width, height }); }} />}
    </div>}
    <button className="uiBtn" onClick={onExport}>{t("Export PDF")}</button>
    {sheets.map((sheet, index) => <Sheet key={sheet.id} {...{ sheet, index, inkBlocks, readOnly, onPaper, inkProps }} />)}
    {!readOnly && <button className="uiBtn notebookAppend" onClick={onAppend}>{t("Add page")}</button>}
  </div>;
}
