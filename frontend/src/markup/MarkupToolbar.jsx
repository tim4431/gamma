// The markup tool strip (docs/dev/handwriting.md, docs/dev/text_boxes.md),
// over the PDF, the notebook view or the notes. App owns the tool state and
// the stroke history; the layers read the armed tool through
// PageToolsContext (markup/PageTools.jsx).
import {
  CopyIcon, ErasePartialIcon, EraserIcon, EraseStrokeIcon, HandIcon, HighlightIcon, LassoIcon, LineWidthIcon, PenIcon,
  RectSelectIcon, RedoIcon, TrashIcon, TypeIcon, UndoIcon, XIcon,
} from "../shared/ui/Icons";
import { HIGHLIGHTER_COLORS, HIGHLIGHTER_OPACITY, MAX_TOOLS, PEN_COLORS, sizesFor, toolId } from "../ink/ink";
import { ColorChoices, ERASER_SIZES, InkTooltips, inkColorName } from "../ink/InkLayer";
import { TextStyleChoices } from "./TextBoxLayer";
import { t } from "../shared/i18n/i18n.js";

const SIZE_LABELS = [t("Small"), t("Medium"), t("Large")];

// The tool strip above the page, Notability-style: a row of tool presets
// (each pen / highlighter with its own colour and width), the eraser, the
// lasso, the Text tool and a hand. Tapping the armed tool again opens its
// options row — colours (palette + custom), widths, duplicate, remove for a
// preset; whole / partial + size for the eraser; freeform / box for the
// lasso; size, colour and background of new text boxes for the Text tool.
// `tools`: the presets; `active`: a preset id, "eraser", "select", "text"
// or null (the hand); `options`: whether the row is open; `textStyle`: the
// selected box's style, else that of new boxes, which onTextStyle(patch)
// sets (and the selected box's). Undo / Redo step the stroke history, or
// with `blockHistory` the page's block history (the Text tool armed, a box
// selected or edited; App routes Ctrl+Z the same way).
export function MarkupToolbar({ tools, active, options, eraserMode, eraserSize, lassoMode, textStyle, blockHistory,
  onPick, onToggleOptions, onChangeTools, onEraser, onLasso, onTextStyle, onClose, onUndo, onRedo, canUndo, canRedo }) {
  const preset = tools.find((t) => t.id === active) || null;
  const tap = (id) => (id === active ? onToggleOptions() : onPick(id));
  // A press on the Text tool leaves the box being typed in focused, so a
  // tap on the armed tool opens the options row that restyles that box.
  const btn = (id, label, icon, extra) => (
    <button key={id} type="button" className={"ctlBtn inkToolBtn" + (active === id ? " modeActive" : "")}
      data-guide={id === "eraser" ? "ink.eraser" : id === "select" ? "ink.lasso" : undefined}
      onMouseDown={id === "text" ? (e) => e.preventDefault() : undefined}
      onClick={() => tap(id)} title={label} aria-label={label} aria-pressed={active === id}>{icon}{extra}</button>
  );
  const edit = (patch) => onChangeTools(tools.map((t) => (t.id === active ? { ...t, ...patch } : t)));
  const duplicate = () => {
    const i = tools.findIndex((t) => t.id === active);
    const copy = { ...tools[i], id: toolId() };
    onChangeTools([...tools.slice(0, i + 1), copy, ...tools.slice(i + 1)]);
    onPick(copy.id, { keepOptions: true, kind: copy.kind });   // not in the list the picker closed over yet
  };
  const remove = () => {
    const i = tools.findIndex((t) => t.id === active);
    const rest = tools.filter((t) => t.id !== active);
    onChangeTools(rest);
    onPick(rest[Math.min(i, rest.length - 1)].id);
  };
  const seg = (on, label, icon, click, title) => (
    <button type="button" className={"ctlBtn inkSegBtn" + (on ? " modeActive" : "")} onClick={click}
      title={title} aria-label={label} aria-pressed={on}>{icon}<span>{label}</span></button>
  );
  const palette = preset ? (preset.kind === "highlighter" ? HIGHLIGHTER_COLORS : PEN_COLORS) : null;
  return (
    <InkTooltips className="pdfInkBar" role="toolbar" aria-label={t("Markup tools")} data-guide="ink.toolbar">
      <div className="pdfInkRow">
        {tools.map((tt, i) => {
          const hl = tt.kind === "highlighter";
          const sizes = sizesFor(tt.kind), k = Math.max(0, sizes.indexOf(tt.size));
          const color = inkColorName(tt.color), size = tt.size;
          // "Pink highlighter · 14 pt · key 7": the digit keys 1–9 arm presets.
          const label = (hl ? t("{color} highlighter · {size} pt", { color, size })
            : tt.brush === "monoline" ? t("{color} monoline pen · {size} pt", { color, size })
            : t("{color} pen · {size} pt", { color, size }))
            + (i < 9 ? t(" · key {key}", { key: i + 1 }) : "")
            + (active === tt.id ? t(" — tap again for options") : "");
          return btn(tt.id, label, hl ? <HighlightIcon size={16} /> : <PenIcon size={16} />,
            <span className="inkToolInk" style={{ background: tt.color, height: hl ? 3 + Math.round(k / 2) : 2 + Math.round(k / 3),
              opacity: hl ? 0.85 : 1 }} />);
        })}
        {btn("eraser", t("Eraser (E) — the pen's eraser end and barrel button erase too"), <EraserIcon size={16} />)}
        {btn("select", t("Lasso (L): circle strokes to select them, then drag the box to move or press Delete"), <LassoIcon size={16} />)}
        {btn("text", t("Text (T): tap the page to type, or drag to set the width"), <TypeIcon size={16} />)}
        <span className="pdfInkSep" />
        <button type="button" className={"ctlBtn inkToolBtn" + (active === null ? " modeActive" : "")}
          onClick={() => onPick(null)} title={t("Hand (V): scroll and select text; a stylus still writes")} aria-label={t("Hand")}
          aria-pressed={active === null}><HandIcon size={16} /></button>
        <span className="pdfInkSep" />
        <button type="button" className="ctlBtn" onClick={onClose} title={t("Close the markup tools (Esc)")} aria-label={t("Close the markup tools")}><XIcon size={16} /></button>
        {/* A press here leaves an open editor (a box's, a note's) focused: the undo is its. */}
        <span className="pdfInkHistory" onMouseDown={(e) => e.preventDefault()}>
          <button type="button" className="ctlBtn" aria-label={blockHistory ? t("Undo") : t("Undo ink")}
            title={blockHistory ? t("Undo the last change to the text boxes and notes") : t("Undo handwriting")}
            disabled={!canUndo} onClick={onUndo}><UndoIcon aria-hidden="true" /></button>
          <button type="button" className="ctlBtn" aria-label={blockHistory ? t("Redo") : t("Redo ink")}
            title={blockHistory ? t("Redo the last change to the text boxes and notes") : t("Redo handwriting")}
            disabled={!canRedo} onClick={onRedo}><RedoIcon aria-hidden="true" /></button>
        </span>
      </div>
      {options && preset ? (
        <div className="pdfInkSub" data-ink-options="tool" data-guide="ink.options">
          {preset.kind === "pen" ? <>
            {seg(preset.brush !== "monoline", t("Pen"), <PenIcon size={14} />, () => edit({ brush: "pen" }), t("Pen: width follows stylus pressure"))}
            {seg(preset.brush === "monoline", t("Monoline"), <LineWidthIcon size={14} />, () => edit({ brush: "monoline" }), t("Monoline: an even line at every pressure"))}
            <span className="pdfInkSep" />
          </> : null}
          <ColorChoices colors={palette} color={preset.color} onColor={(color) => edit({ color })} />
          <span className="pdfInkSep" />
          {sizesFor(preset.kind).map((sz, i) => (
            <button key={sz} type="button" className={"ctlBtn inkSizeBtn" + (preset.size === sz ? " modeActive" : "")}
              onClick={() => edit({ size: sz })} title={t("{sz} pt", { sz: sz })} aria-label={t("Width {sz} pt", { sz: sz })}>
              <span className="inkSizeDot" style={{ width: 4 + i * 2, height: 4 + i * 2, background: preset.color,
                opacity: preset.kind === "highlighter" ? HIGHLIGHTER_OPACITY + 0.2 : 1 }} />
            </button>
          ))}
          <span className="pdfInkSep" />
          <button type="button" className="ctlBtn" onClick={duplicate} disabled={tools.length >= MAX_TOOLS}
            title={t("Duplicate: a second copy of this tool to give its own colour and width")} aria-label={t("Duplicate tool")}><CopyIcon size={16} /></button>
          <button type="button" className="ctlBtn" onClick={remove} disabled={tools.length <= 1}
            title={t("Remove this tool from the strip")} aria-label={t("Remove tool")}><TrashIcon size={16} /></button>
        </div>
      ) : null}
      {options && active === "eraser" ? (
        <div className="pdfInkSub" data-ink-options="eraser">
          {seg(eraserMode !== "partial", t("Whole strokes"), <EraseStrokeIcon size={14} />, () => onEraser({ mode: "stroke" }),
            t("Whole strokes: anything the eraser touches goes entirely"))}
          {seg(eraserMode === "partial", t("Partial"), <ErasePartialIcon size={14} />, () => onEraser({ mode: "partial" }),
            t("Partial: erase just what the eraser passes over (strokes are cut)"))}
          <span className="pdfInkSep" />
          {ERASER_SIZES.map((px, i) => (
            <button key={px} type="button" className={"ctlBtn inkSizeBtn" + (eraserSize === i ? " modeActive" : "")}
              onClick={() => onEraser({ size: i })} title={t("{i} eraser", { i: SIZE_LABELS[i] })} aria-label={t("{i} eraser", { i: SIZE_LABELS[i] })}>
              <span className="inkSizeDot inkEraserDot" style={{ width: 6 + i * 4, height: 6 + i * 4 }} />
            </button>
          ))}
        </div>
      ) : null}
      {options && active === "select" ? (
        <div className="pdfInkSub" data-ink-options="select">
          {seg(lassoMode !== "box", t("Freeform"), <LassoIcon size={14} />, () => onLasso("free"), t("Freeform: draw a loop around the strokes"))}
          {seg(lassoMode === "box", t("Box"), <RectSelectIcon size={14} />, () => onLasso("box"), t("Box: drag a rectangle over the strokes"))}
        </div>
      ) : null}
      {options && active === "text" ? (
        // A press here keeps the focus in the box being typed in, which the row restyles too.
        <div className="pdfInkSub" data-ink-options="text" onMouseDown={(e) => e.preventDefault()}>
          <TextStyleChoices kind="size" style={textStyle} onStyle={onTextStyle} />
          <span className="pdfInkSep" />
          <TextStyleChoices kind="color" style={textStyle} onStyle={onTextStyle} />
          <span className="pdfInkSep" />
          <TextStyleChoices kind="bg" style={textStyle} onStyle={onTextStyle} />
        </div>
      ) : null}
    </InkTooltips>
  );
}
