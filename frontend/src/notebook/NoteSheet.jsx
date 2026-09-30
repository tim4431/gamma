// A sheet in the notes view (docs/dev/notebooks.md): drawn where it stands
// among the page's blocks, fitted to the column — its paper, with the ink
// layer the notebook view's sheets have — and under it a row of its tools:
// the pen (the ink strip), its paper, a replay of all its handwriting
// (ink/inkReplay.js, under the sheet's id), a new page after it, and the
// switch to the notebook view. In the notebook view the viewer draws the
// sheets and the notes list them as rows. App owns the tree, the tools, the
// stroke history and the commits, and hands them down through
// NoteSheetContext.
import React, { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { InkLayer, InkReplayButton, Strokes, useInkVersion, useReplayOf } from "../ink/InkLayer";
import * as inkReplay from "../ink/inkReplay";
import * as inkStore from "../ink/inkStore";
import { newCanvasInk } from "../ink/ink";
import { PaperBackground, PaperMenu } from "./NotebookViewer";
import { normalizePaper } from "./notebook";
import { NotebookIcon, PenIcon, PlusIcon, SheetIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

// {readOnly, inkBySheet, tool, penTool, penOnly, pressure, eraserMode,
// eraserSize, lassoMode, selection, flash, inkOpen, the InkLayer's
// handlers (onStroke …), onPen(), onPaper(sheetId, paper),
// onPaperAll(paper), onAddAfter(sheetId), onNotebookView() (a page
// without a PDF)}; null in the notebook view.
export const NoteSheetContext = createContext(null);

const MAX_SCALE = 1.5; // css px per pt: a page never grows past this in a wide column
const EMPTY = [];

// The same list while its items are the same objects.
function useSame(list) {
  const ref = useRef(list);
  if (list.length !== ref.current.length || list.some((x, i) => x !== ref.current[i])) ref.current = list;
  return ref.current;
}

export function NoteSheet({ block, number }) {
  const ctx = useContext(NoteSheetContext);
  useInkVersion();
  const boxRef = useRef(null);
  const wrapRef = useRef(null);
  const [boxW, setBoxW] = useState(0);
  const [paperOpen, setPaperOpen] = useState(false);
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const measure = () => setBoxW(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const paper = useMemo(() => normalizePaper(block.properties?.sheet), [block.properties?.sheet]);
  const groups = ctx?.inkBySheet.get(block.id) || EMPTY;
  // Its handwriting as one drawing, for the replay: every group's strokes.
  const inks = useSame(groups.map((b) => inkStore.inkFor(b)).filter(Boolean));
  const drawing = useMemo(() => ({ ...newCanvasInk(paper.width, paper.height), strokes: inks.flatMap((ink) => ink.strokes || []) }),
    [inks, paper.width, paper.height]);
  const playing = useReplayOf([block.id]);
  // a drawing that changes ends its replay
  useEffect(() => () => inkReplay.stop(block.id), [drawing, block.id]);
  const replay = {
    playing: !!playing, frame: playing?.frame || null,
    play: () => inkReplay.play(block.id, drawing), stop: () => inkReplay.stop(block.id),
  };
  if (!ctx) return null;
  const scale = boxW ? Math.min(MAX_SCALE, boxW / paper.width) : 0;
  const editable = !ctx.readOnly;
  return (
    <div ref={boxRef} className={"noteSheet" + (ctx.tool ? " inkArmed" : "") + (ctx.tool && !ctx.penOnly ? " inkTouchDraw" : "")}
      data-sheet-id={block.id}>
      {scale ? (
        <div ref={wrapRef} className="nbSheet" style={{ width: `${paper.width * scale}px`, height: `${paper.height * scale}px` }}
          aria-label={t("Page {n}", { n: number })}>
          <PaperBackground paper={paper} />
          {replay.frame ? (
            <svg className="inkLayer" viewBox={`0 0 ${paper.width} ${paper.height}`} preserveAspectRatio="none" aria-hidden="true">
              <Strokes ink={replay.frame} />
            </svg>
          ) : groups.length || editable ? (
            <InkLayer pageNumber={block.id} wrapRef={wrapRef} width={paper.width} height={paper.height}
              blocks={groups} tool={editable ? ctx.tool : null} penTool={editable ? ctx.penTool : null}
              penOnly={ctx.penOnly} pressure={ctx.pressure} eraserMode={ctx.eraserMode} eraserSize={ctx.eraserSize}
              lassoMode={ctx.lassoMode} selection={ctx.selection?.page === block.id ? ctx.selection : null}
              flash={ctx.flash && groups.some((g) => g.id === ctx.flash.id) ? ctx.flash : null}
              onStroke={editable ? ctx.onStroke : undefined} onErase={editable ? ctx.onErase : undefined}
              onErasePartial={editable ? ctx.onErasePartial : undefined} onSelect={editable ? ctx.onSelect : undefined}
              onAction={editable ? ctx.onAction : undefined} onMoveSelection={editable ? ctx.onMoveSelection : undefined}
              onJump={ctx.onJump} />
          ) : null}
          <span className="nbSheetNo" aria-hidden="true">{number}</span>
        </div>
      ) : null}
      <div className="noteSheetBar">
        {editable ? (
          <button type="button" className={"ctlBtn" + (ctx.inkOpen ? " modeActive" : "")} aria-pressed={ctx.inkOpen}
            onClick={ctx.onPen} aria-label={t("Write on this page")}
            title={ctx.inkOpen ? t("Close the handwriting tools (Esc)") : t("Handwriting: write on this page with a pen, highlighter or eraser")}>
            <PenIcon size={16} />
          </button>
        ) : null}
        {editable ? (
          <button type="button" className={"ctlBtn" + (paperOpen ? " modeActive" : "")} aria-expanded={paperOpen}
            onClick={() => setPaperOpen((v) => !v)} aria-label={t("Paper of this page")} title={t("Paper: size, pattern and colour of this page")}>
            <SheetIcon size={16} />
          </button>
        ) : null}
        {drawing.strokes.length ? <InkReplayButton replay={replay} className="ctlBtn" /> : null}
        {editable ? (
          <button type="button" className="ctlBtn" onClick={() => ctx.onAddAfter(block.id)}
            aria-label={t("Add page below")} title={t("Add a page after this one")}>
            <PlusIcon size={16} />
          </button>
        ) : null}
        {ctx.onNotebookView ? (
          <button type="button" className="ctlBtn" onClick={ctx.onNotebookView}
            aria-label={t("Notebook view")} title={t("Notebook view: the pages beside the notes")}>
            <NotebookIcon size={16} />
          </button>
        ) : null}
      </div>
      {paperOpen && editable ? (
        <PaperMenu paper={paper} number={number}
          onChange={(next) => ctx.onPaper(block.id, next)}
          onApplyAll={() => ctx.onPaperAll(paper)}
          onClose={() => setPaperOpen(false)} />
      ) : null}
    </div>
  );
}
