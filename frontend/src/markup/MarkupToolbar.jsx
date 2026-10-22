// The markup tool strip (docs/dev/handwriting.md, docs/dev/text_boxes.md),
// over the PDF, the notebook view or the notes. The tool state is
// ink/useInkStrip.js's and the stroke history ink/useInkDocument.js's;
// the layers read the armed tool through PageToolsContext
// (markup/PageTools.jsx).
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CheckIcon, ChevronLeftIcon, ChevronRightIcon, DuplicateIcon, ErasePartialIcon, EraserIcon, EraseStrokeIcon, EyeIcon, EyeOffIcon, HandIcon,
  HighlightIcon, HistoryIcon, LassoIcon, MinusIcon, MoreIcon, PaintBucketIcon, PenIcon, PlusIcon, RectSelectIcon, RedoIcon, RulerIcon,
  SettingsIcon, ShapeArrowIcon, ShapeEllipseIcon, ShapeLineIcon, ShapeRectIcon, ShapesIcon, StampLineIcon, TrashIcon, TypeIcon, UndoIcon,
  XIcon, ZoomWindowIcon,
} from "../shared/ui/Icons";
import { ContextMenu, MenuDivider, MenuItem, SubMenuItem, useMenuTrigger } from "../shared/ui/Menus";
import { CommandMenuItem } from "../app/CommandMenuItem.jsx";
import {
  DEFAULT_NIB, DEFAULT_TOOLS, MAX_TOOLS, NIB_MAX, NIB_MIN, NIB_STEP, defaultOpacity, presetSmoothing, sizesFor, toolId, visibleTools,
} from "../ink/ink";
import { ColorChoices, ERASER_SIZES, InkTooltips, inkColorName, swallowClick } from "../ink/InkLayer";
import { InkLineChoices, InkStyleMenu, InkStylePicker, NibPreview, brushValue } from "../ink/InkStylePicker.jsx";
import { nativeHost } from "../shared/lib/host.js";
import { HOLD_MS, HOLD_SLOP } from "../shared/ui/press.js";
import { pickerColors } from "../shared/model/palette.js";
import { usePalette } from "../shared/ui/paletteContext.js";
import { TextStyleChoices } from "./TextBoxLayer";
import { t, T } from "../shared/i18n/i18n.js";

const SIZE_LABELS = [t("Small"), t("Medium"), t("Large")];

// The tool strip above the page, Notability-style: a row of tool presets
// (each pen / highlighter with its own colour and width), the eraser, the
// lasso, the Text tool, a hand, More (⋯) and close, Undo / Redo last.
// Tapping the armed tool again opens its options row — for a preset three
// captioned sub-rows, Style (a pen's Style picker — Pen, Ballpoint, Brush,
// Pencil, Calligraphy, ink/InkStylePicker.jsx — or a highlighter's Straight trio, then
// the Line trio and the Smoothing ladder), Color (palette + custom) and Width
// (eight dots, duplicate, remove and the Tool menu: move, hide, hidden
// tools, reset); whole / partial + size for the eraser; freeform / box for
// the lasso; size, colour and background of new text boxes for the Text
// tool.
// `tools`: the presets, hidden ones included (`visibleTools` filters the
// row); `active`: a preset id, "eraser", "select", "text", "shape" (the
// Shapes tool) or null (the hand); `options`: which row is open — false,
// true (the armed tool's) or "ruler" (the ruler's); `textStyle`: the selected
// box's style, else that of new boxes, which onTextStyle(patch) sets (and
// the selected box's). onChangeTools(list | fn) writes the preset list
// (ids stable; a pill's deferred action passes an updater). Undo / Redo
// step the stroke history, or with `blockHistory` the page's block history
// (the Text tool armed, a box selected or edited; the strip's keys,
// ink/useStripKeys.js, route Ctrl+Z the same way). onStatus(text,
// action?) is App's setStatus; cmdCtx and
// bindings feed the More menu's command rows (app/CommandMenuItem.jsx);
// onOpenSettings() opens Settings at its Handwriting section.
// A preset's row also carries its Smoothing ladder (`stabilize` 0–4,
// ink/stabilizer.js) and a highlighter's Straight trio (`straight`: hold /
// always / off, ink/finishStroke.js). The eraser's row carries, after the
// sizes, the account's Erases filter (`eraserFilter`) and the Back to last
// tool toggle (`eraserReturn`), both through onEraser({filter} | {return}).
// The modifiers (docs/dev/handwriting.md "What the user sees"): `ruler`
// (useInkRuler.js's inkRuler, or null), `rulerUnits` and onToggleRuler(),
// onRulerOptions({snap} | {units} | {hide: true}), onStampLine(); the Shapes
// tool's `shapeKind`, `shapeFill`, onShape({kind} | {fill}),
// onToggleShape() and `stylusPen` (the preset a stylus writes with, whose
// look its shapes take); the zoom window's `zoomOpen`, `zoomAvailable` (a
// PDF page is in view), `phone` and onToggleZoom(). Each shows in the second
// group as a pressed button only while on, and has its row in More;
// onToggleOptions(which) opens or closes the armed tool's row, or with
// "ruler" the ruler's.
const SHAPE_CHOICES = [
  ["line", T("Line"), ShapeLineIcon, T("Line: drag from one end to the other")],
  ["arrow", T("Arrow"), ShapeArrowIcon, T("Arrow: drag from the tail to the tip")],
  ["rect", T("Rectangle"), ShapeRectIcon, T("Rectangle: drag from corner to corner; Shift makes a square")],
  ["ellipse", T("Ellipse"), ShapeEllipseIcon, T("Ellipse: drag across its box; Shift makes a circle")],
];
const SMOOTHING = [0, 1, 2, 3, 4];
const STRAIGHT = [
  ["hold", T("Hold"), T("Straight highlighter — Hold: keep the pen still at the end of a stroke to straighten it")],
  ["always", T("Always"), T("Straight highlighter — Always: every nearly straight highlight becomes a straight line on lift")],
  ["off", T("Off"), T("Straight highlighter — Off: highlights stay as drawn")],
];
const ERASES = [
  ["all", T("All"), null, T("Erases everything the eraser touches")],
  ["pen", T("Pens"), PenIcon, T("Erases pen strokes only; highlights stay")],
  ["highlighter", T("Highlighters"), HighlightIcon, T("Erases highlights only; pen strokes stay")],
];
// A press on a menu row or a tool button leaves an open editor (a box's, a
// note's) focused.
const keepFocus = (e) => e.preventDefault();

// A preset's name in words, the colour and the kind: "Blue pen", "Red
// ballpoint pen", "Green brush pen", "Black pencil", "Pink highlighter" —
// what its button, the Hidden tools menu and the strip's pills call it (the
// line is not named). The stored brush is still `monoline`, a word that
// never shows.
export function inkToolName(preset) {
  const color = inkColorName(preset.color);
  if (preset.kind === "highlighter") return t("{color} highlighter", { color });
  const brush = brushValue(preset.brush);
  if (brush === "monoline") return t("{color} ballpoint pen", { color });
  if (brush === "brush") return t("{color} brush pen", { color });
  if (brush === "pencil") return t("{color} pencil", { color });
  if (brush === "calligraphy") return t("{color} calligraphy pen", { color });
  return t("{color} pen", { color });
}
// A calligraphy pen's Style row adds its Nib angle (NIB_MIN … NIB_MAX in
// NIB_STEP steps: − and + around a readout, for a mouse and a finger alike,
// then the nib drawn at the angle) and, under a host whose Pencil reports
// its barrel roll (`pencilRoll`, an Apple Pencil Pro), Lock angle. A
// calligraphy pen whose Smoothing was never set smooths at 2 (ink.js
// CALLIGRAPHY_SMOOTHING, presetSmoothing): a default, not part of the
// edit, so the pen keeps the smoothing it had when it leaves Calligraphy.
function NibAngle({ preset, edit, seg }) {
  const nib = Number.isFinite(preset.nib) ? preset.nib : DEFAULT_NIB;
  const step = (by) => edit({ nib: Math.max(NIB_MIN, Math.min(NIB_MAX, nib + by)) });
  return <>
    <span className="inkNib" role="group" aria-label={t("Nib angle")} title={t("Nib angle: the slant of the flat nib, −60° to 60°")}>
      <button type="button" className="ctlBtn" aria-label={t("Nib angle −15°")} title={t("Nib angle −15°")} disabled={nib <= NIB_MIN}
        onClick={() => step(-NIB_STEP)}><MinusIcon size={16} aria-hidden="true" /></button>
      <span className="inkNibReadout">{`${nib}°`}</span>
      <button type="button" className="ctlBtn" aria-label={t("Nib angle +15°")} title={t("Nib angle +15°")} disabled={nib >= NIB_MAX}
        onClick={() => step(NIB_STEP)}><PlusIcon size={16} aria-hidden="true" /></button>
      <NibPreview nib={nib} />
    </span>
    {nativeHost()?.pencilRoll ? seg(!!preset.nibLock, t("Lock angle"), null, () => edit({ nibLock: !preset.nibLock }),
      t("Lock angle: rolling Apple Pencil Pro does not turn the nib")) : null}
  </>;
}
// "Blue pen · 2 pt".
const toolLabel = (preset) => t("{name} · {size} pt", { name: inkToolName(preset), size: preset.size });

// `tools` with the preset `id` moved to the visible slot `to` (0 … the
// visible count: before the visible preset at that index, or last) — the
// drop of a drag, and Move left / right. Hidden presets keep their places
// between the visible ones; ids never change. The same list when the slot
// is where it already stands.
export function moveTool(tools, id, to) {
  const visible = visibleTools(tools);
  const from = visible.findIndex((x) => x.id === id);
  if (from < 0 || to === from || to === from + 1) return tools;
  const item = tools.find((x) => x.id === id);
  const rest = tools.filter((x) => x.id !== id);
  const restVisible = visible.filter((x) => x.id !== id);
  const j = to > from ? to - 1 : to;
  const at = j >= restVisible.length ? rest.length : rest.indexOf(restVisible[j]);
  return [...rest.slice(0, at), item, ...rest.slice(at)];
}
// The pill after a move: "Blue pen is now key 3.", or "Blue pen moved."
// past the digit keys.
function movedStatus(tools, id) {
  const name = inkToolName(tools.find((x) => x.id === id));
  const n = visibleTools(tools).findIndex((x) => x.id === id) + 1;
  return n <= 9 ? t("{name} is now key {n}.", { name, n }) : t("{name} moved.", { name });
}

// The handwriting strip's reorder-by-drag (docs/dev/handwriting.md
// "Reorder by dragging"): a mouse past DRAG_PX, or a finger or pen held
// HOLD_MS (shared/ui/press.js's long press, with its HOLD_SLOP), takes the
// pointer (setPointerCapture) and rides a ghost of the preset along the
// row; `to` is the visible slot under the pointer, drawn as a 2 px bar.
// The release inside the strip writes the order and swallows the click
// the button would get; outside, or Escape, leaves it.
// A hold that never moved is the button's ordinary click (a tap arms the
// tool). Returns the preset buttons' props, the live drag for the row
// (ghost and bar) and the dragged id.
const DRAG_PX = 6;
function usePresetDrag({ tools, rowRef, onChangeTools, onStatus }) {
  const [drag, setDrag] = useState(null);     // {id, x, y, to, bar: {left, top, height}} while live
  const press = useRef(null);                 // the press: {id, pointerId, el, x, y, timer, live, moved}
  const latest = useRef(null);
  latest.current = { tools, onChangeTools, onStatus };
  const end = useCallback(() => {
    const p = press.current;
    if (!p) return;
    clearTimeout(p.timer);
    try { p.el.releasePointerCapture(p.pointerId); } catch {}
    press.current = null;
    setDrag(null);
  }, []);
  // The visible slot a point drops into, and where the bar for it goes
  // (in the row's own coordinates).
  const slotAt = (x) => {
    const buttons = [...(rowRef.current?.querySelectorAll("[data-preset]") || [])];
    const rects = buttons.map((b) => b.getBoundingClientRect());
    let to = rects.findIndex((r) => x < r.left + r.width / 2);
    if (to < 0) to = rects.length;
    const row = rowRef.current.getBoundingClientRect();
    const r = rects[Math.min(to, rects.length - 1)];
    const left = (to < rects.length ? r.left : r.right) - row.left;
    return { to, bar: { left: Math.round(left) - 1, top: Math.round(r.top - row.top), height: Math.round(r.height) } };
  };
  const begin = (p) => {
    p.live = true;
    latest.current.onStatus?.(t("Drop to reorder."));
    setDrag({ id: p.id, x: p.x, y: p.y, ...slotAt(p.x) });
  };
  useEffect(() => {
    const escape = (e) => { if (e.key === "Escape" && press.current?.live) { e.stopPropagation(); end(); } };
    window.addEventListener("keydown", escape, true);
    window.addEventListener("blur", end);
    return () => { window.removeEventListener("keydown", escape, true); window.removeEventListener("blur", end); end(); };
  }, [end]);
  const props = (id) => ({
    "data-preset": id,
    onPointerDown: (e) => {
      if (press.current || !e.isPrimary || (e.pointerType === "mouse" && e.button !== 0)) return;
      const p = { id, pointerId: e.pointerId, el: e.currentTarget, x: e.clientX, y: e.clientY, timer: 0, live: false, moved: false };
      press.current = p;
      try { e.currentTarget.setPointerCapture(e.pointerId); } catch {}
      if (e.pointerType !== "mouse") p.timer = setTimeout(() => { if (press.current === p) begin(p); }, HOLD_MS);
    },
    onPointerMove: (e) => {
      const p = press.current;
      if (!p || p.pointerId !== e.pointerId) return;
      const d = Math.hypot(e.clientX - p.x, e.clientY - p.y);
      if (!p.live) {
        if (p.timer) { if (d > HOLD_SLOP) { clearTimeout(p.timer); p.timer = 0; } return; }  // a held finger that wandered: a plain press
        if (e.pointerType === "mouse" && d >= DRAG_PX) begin(p); else return;
      }
      if (d >= DRAG_PX) p.moved = true;
      setDrag({ id: p.id, x: e.clientX, y: e.clientY, ...slotAt(e.clientX) });
    },
    onPointerUp: (e) => {
      const p = press.current;
      if (!p || p.pointerId !== e.pointerId) return;
      if (!p.live || !p.moved) { end(); return; }   // a click, or a hold that never moved: the button's own tap
      swallowClick();
      const bar = rowRef.current?.closest(".pdfInkBar")?.getBoundingClientRect();
      const inside = bar && e.clientX >= bar.left && e.clientX <= bar.right && e.clientY >= bar.top && e.clientY <= bar.bottom;
      const { to } = slotAt(e.clientX);
      end();
      if (!inside) return;
      const { tools: list, onChangeTools: write, onStatus: say } = latest.current;
      const moved = moveTool(list, p.id, to);
      if (moved === list) return;
      write(moved);
      say?.(movedStatus(moved, p.id));
    },
    onPointerCancel: (e) => { if (press.current?.pointerId === e.pointerId) { if (press.current.live) swallowClick(); end(); } },
    onLostPointerCapture: (e) => { if (press.current?.pointerId === e.pointerId && !press.current.live) end(); },
  });
  return { props, drag };
}

export function MarkupToolbar({ tools, active, options, eraserMode, eraserSize, eraserReturn = false, eraserFilter = "all", lassoMode,
  textStyle, blockHistory, onPick, onToggleOptions, onChangeTools, onEraser, onLasso, onTextStyle, onClose, onUndo, onRedo, canUndo, canRedo,
  onOpenSettings, onStatus, cmdCtx, bindings,
  ruler = null, rulerUnits = "pt", onToggleRuler, onRulerOptions, onStampLine,
  shapeKind = "line", shapeFill = false, onShape, onToggleShape, stylusPen = null,
  zoomOpen = false, zoomAvailable = false, phone = false, onToggleZoom }) {
  const visible = visibleTools(tools);
  const hidden = tools.filter((x) => x.hidden);
  const preset = tools.find((x) => x.id === active) || null;
  const tap = (id) => (id === active ? onToggleOptions() : onPick(id));
  const rowRef = useRef(null);
  // The strip's menus, More (`"more"`), a preset's Tool menu (`"tool"`) and
  // its Style picker's (`"style"`), one at a time under their buttons
  // (Menus.jsx useMenuTrigger): they open on the press and are rendered
  // after the InkTooltips element, so no row the pointer crosses raises a
  // strip tooltip over them. The rows call closeMenu before they act.
  const menus = useMenuTrigger();
  const closeMenu = menus.close;
  // The Tool and Style menus belong to the open options row of a preset.
  useEffect(() => {
    if ((menus.kind === "tool" || menus.kind === "style") && !(options === true && preset)) closeMenu();
  }, [menus.kind, closeMenu, options, preset]);
  const { props: dragProps, drag } = usePresetDrag({ tools, rowRef, onChangeTools, onStatus });
  // A press on the Text tool leaves the box being typed in focused, so a
  // tap on the armed tool opens the options row that restyles that box.
  const btn = (id, label, icon, extra, more) => (
    <button key={id} type="button" className={"ctlBtn inkToolBtn" + (active === id ? " modeActive" : "") + (drag?.id === id ? " inkDragging" : "")}
      data-guide={id === "eraser" ? "ink.eraser" : id === "select" ? "ink.lasso" : id === "text" ? "ink.text" : id === "shape" ? "ink.shape" : undefined}
      onMouseDown={id === "text" ? keepFocus : undefined}
      onClick={() => tap(id)} title={label} aria-label={label} aria-pressed={active === id} {...more}>{icon}{extra}</button>
  );
  const edit = (patch) => onChangeTools(tools.map((x) => (x.id === active ? { ...x, ...patch } : x)));
  const duplicate = () => {
    const i = tools.findIndex((x) => x.id === active);
    const copy = { ...tools[i], id: toolId() };
    onChangeTools([...tools.slice(0, i + 1), copy, ...tools.slice(i + 1)]);
    onPick(copy.id, { keepOptions: true, kind: copy.kind });   // not in the list the picker closed over yet
  };
  // The visible neighbour takes over when the armed preset leaves the row
  // (the hand when none is left). Remove and Hide are both greyed at the
  // last visible preset, hidden ones or not: the row never empties.
  const neighbour = () => {
    const i = visible.findIndex((x) => x.id === active);
    const rest = visible.filter((x) => x.id !== active);
    return rest.length ? rest[Math.min(i, rest.length - 1)].id : null;
  };
  const remove = () => {
    const next = neighbour();
    onChangeTools(tools.filter((x) => x.id !== active));
    onPick(next);
  };
  // Back on the row where it was; an updater, since the pill's Show runs later.
  const show = (id) => onChangeTools((list) => list.map((x) => {
    if (x.id !== id) return x;
    const { hidden: _hidden, ...shown } = x;
    return shown;
  }));
  const hide = () => {
    const id = active, name = inkToolName(preset), next = neighbour();
    onChangeTools(tools.map((x) => (x.id === id ? { ...x, hidden: true } : x)));
    onPick(next);
    onStatus?.(t("{name} hidden.", { name }), { label: t("Show"), run: () => show(id) });
  };
  const move = (dir) => {
    const from = visible.findIndex((x) => x.id === active);
    const moved = moveTool(tools, active, dir < 0 ? from - 1 : from + 2);
    if (moved === tools) return;
    onChangeTools(moved);
    onStatus?.(movedStatus(moved, active));
  };
  // Colour, width and style back to the default pen or highlighter of its kind.
  const reset = () => {
    const d = DEFAULT_TOOLS.find((x) => x.kind === preset.kind);
    onChangeTools(tools.map((x) => (x.id === active ? { id: x.id, kind: x.kind, color: d.color, size: d.size } : x)));
  };
  const seg = (on, label, icon, click, title) => (
    <button type="button" className={"ctlBtn inkSegBtn" + (on ? " modeActive" : "")} onClick={click}
      title={title} aria-label={label} aria-pressed={on}>{icon}<span>{label}</span></button>
  );
  const colors = usePalette();
  const palette = preset ? pickerColors(colors, preset.kind === "highlighter" ? "highlighter" : "pen") : null;
  const presetOpacity = preset ? preset.opacity ?? defaultOpacity(preset.kind) : 1;
  const row = (caption, className, children) => (
    <div className={"inkOptRow " + className}><span className="inkOptCaption" aria-hidden="true">{caption}</span>{children}</div>
  );
  const visibleIndex = preset ? visible.findIndex((x) => x.id === active) : -1;
  const menuRow = (id, extra) => (
    <CommandMenuItem key={id} id={id} ctx={cmdCtx} bindings={bindings} onRun={closeMenu} onMouseDown={keepFocus} {...extra} />
  );
  // A modifier's More row: a checkbox naming its key, ticked while on.
  const modifierRow = (Icon, label, key, on, run, extra) => (
    <MenuItem icon={Icon} role="menuitemcheckbox" aria-checked={on} keys={key} onMouseDown={keepFocus}
      trailing={on ? <><span className="ctxMenuKey">{key}</span><CheckIcon size={14} className="ctxMenuCheck" /></> : undefined}
      onClick={() => { closeMenu(); run?.(); }} {...extra}>{label}</MenuItem>
  );
  return <>
    <InkTooltips className="pdfInkBar" role="toolbar" aria-label={t("Markup tools")} data-guide="ink.toolbar">
      <div className="pdfInkRow" ref={rowRef}>
        {visible.map((tt, i) => {
          const hl = tt.kind === "highlighter";
          const sizes = sizesFor(tt.kind), k = Math.max(0, sizes.indexOf(tt.size));
          // "Pink highlighter · 14 pt · key 7": the digit keys 1–9 arm presets.
          const label = toolLabel(tt)
            + (i < 9 ? t(" · key {key}", { key: i + 1 }) : "")
            + (active === tt.id ? t(" — tap again for options") : "");
          return btn(tt.id, label, hl ? <HighlightIcon size={16} /> : <PenIcon size={16} />,
            <span className="inkToolInk" style={{ background: tt.color, height: hl ? 3 + Math.round(k / 2) : 2 + Math.round(k / 3),
              opacity: hl ? 0.85 : 1 }} />, dragProps(tt.id));
        })}
        <span className="pdfInkSep" />
        {btn("eraser", t("Eraser (E) — the pen's eraser end and barrel button erase too"), <EraserIcon size={16} />)}
        {btn("select", t("Lasso (L): circle strokes to select them, then drag the box to move or press Delete"), <LassoIcon size={16} />)}
        {btn("text", t("Text (T): tap the page to type, or drag to set the width"), <TypeIcon size={16} />)}
        {/* The modifiers, each only while on (More turns them on): a tap opens the ruler's row, the Shapes tool's row, or closes the zoom window. */}
        {ruler ? (
          <button type="button" className="ctlBtn inkToolBtn modeActive" aria-pressed="true" aria-label={t("Ruler")}
            title={t("Ruler (R): write straight along its edges — tap for its options")} data-guide="ink.ruler"
            onClick={() => onToggleOptions("ruler")}><RulerIcon size={16} /></button>
        ) : null}
        {active === "shape" ? btn("shape", t("Shapes (S): drag to draw a shape — tap again for its options"), <ShapesIcon size={16} />, null,
          { "aria-label": t("Shapes") }) : null}
        {zoomOpen && onToggleZoom ? (
          <button type="button" className="ctlBtn inkToolBtn modeActive" aria-pressed="true" aria-label={t("Zoom window")}
            title={t("Zoom window (Z): write large in the panel below — tap to close")} onClick={onToggleZoom}><ZoomWindowIcon size={16} /></button>
        ) : null}
        <span className="pdfInkSep" />
        <button type="button" className={"ctlBtn inkToolBtn" + (active === null ? " modeActive" : "")}
          onClick={() => onPick(null)} title={t("Hand (V): scroll and select text; a stylus still writes")} aria-label={t("Hand")}
          aria-pressed={active === null}><HandIcon size={16} /></button>
        <span className="pdfInkSep" />
        <button type="button" className="ctlBtn" {...menus.trigger("more")} title={t("More handwriting tools")} aria-label={t("More handwriting tools")}
          data-guide="ink.more"><MoreIcon size={16} /></button>
        <button type="button" className="ctlBtn" onClick={onClose} title={t("Close the markup tools (Esc)")} aria-label={t("Close the markup tools")}><XIcon size={16} /></button>
        <span className="pdfInkSep pdfInkSepEnd" />
        {/* A press here leaves an open editor (a box's, a note's) focused: the undo is its. */}
        <span className="pdfInkHistory" onMouseDown={keepFocus}>
          <button type="button" className="ctlBtn" aria-label={blockHistory ? t("Undo") : t("Undo ink")}
            title={blockHistory ? t("Undo the last change to the text boxes and notes") : t("Undo handwriting")}
            disabled={!canUndo} onClick={onUndo}><UndoIcon aria-hidden="true" /></button>
          <button type="button" className="ctlBtn" aria-label={blockHistory ? t("Redo") : t("Redo ink")}
            title={blockHistory ? t("Redo the last change to the text boxes and notes") : t("Redo handwriting")}
            disabled={!canRedo} onClick={onRedo}><RedoIcon aria-hidden="true" /></button>
        </span>
        {drag ? <span className="inkDropBar" style={{ left: drag.bar.left, top: drag.bar.top, height: drag.bar.height }} aria-hidden="true" /> : null}
      </div>
      {options === true && preset ? (
        <div className="pdfInkSub inkPresetOptions" data-ink-options="tool" data-guide="ink.options">
          {row(t("Style"), "inkOptStyle", <>
            {preset.kind === "pen" ? (
              // the Style picker: its menu opens on the press, after the InkTooltips host (`menus`)
              <>
                <InkStylePicker brush={preset.brush} trigger={menus.trigger("style")} />
                {brushValue(preset.brush) === "calligraphy" ? <><span className="pdfInkSep" /><NibAngle preset={preset} edit={edit} seg={seg} /></> : null}
              </>
            ) : (
              <span className="inkStraight" role="group" aria-label={t("Straight highlighter")}>
                {STRAIGHT.map(([value, label, title]) => seg((preset.straight || "hold") === value, t(label), null,
                  () => edit({ straight: value }), t(title)))}
              </span>
            )}
            <span className="pdfInkSep" />
            {/* the line, pens and highlighters alike: the dash pattern follows the width */}
            <InkLineChoices value={preset.dash ?? null} onLine={(dash) => edit({ dash })} />
            <span className="pdfInkSep" />
            {/* Smoothing: five hollow dots, Off and the four string lengths (ink/stabilizer.js). */}
            <span className="inkSmoothing" role="group" aria-label={t("Smoothing")}>
              {SMOOTHING.map((level) => {
                const on = presetSmoothing(preset) === level;
                const label = level ? t("Smoothing {level} of 4", { level }) : t("Smoothing off");
                return <button key={level} type="button" className={"ctlBtn inkSizeBtn" + (on ? " modeActive" : "")}
                  onClick={() => edit({ stabilize: level })} aria-pressed={on} aria-label={label}
                  title={level ? t("Smoothing {level} of 4: the pen pulls the line behind it, so a shaky hand draws a calm line", { level })
                    : t("Smoothing off: the line follows the pen exactly")}>
                  <span className="inkSizeDot inkSmoothDot" style={{ width: 4 + level * 2, height: 4 + level * 2 }} />
                </button>;
              })}
            </span>
          </>)}
          {row(t("Color"), "inkOptColor",
            <ColorChoices colors={palette} color={preset.color} alpha={presetOpacity}
              onColor={(color, alpha) => edit({ color, opacity: alpha })} />)}
          {row(t("Width"), "inkOptWidth", <>
            {sizesFor(preset.kind).map((sz, i) => (
              <button key={sz} type="button" className={"ctlBtn inkSizeBtn" + (preset.size === sz ? " modeActive" : "")}
                onClick={() => edit({ size: sz })} title={t("Width {sz} pt · [ ]", { sz })} aria-label={t("Width {sz} pt", { sz })}>
                <span className="inkSizeDot" style={{ width: 4 + i * 2, height: 4 + i * 2, background: preset.color,
                  opacity: Math.min(1, presetOpacity + (preset.kind === "highlighter" ? 0.2 : 0)) }} />
              </button>
            ))}
            <span className="pdfInkSep" />
            <button type="button" className="ctlBtn" onClick={duplicate} disabled={tools.length >= MAX_TOOLS}
              title={t("Duplicate: a second copy of this tool to give its own colour and width")} aria-label={t("Duplicate tool")}><DuplicateIcon size={16} /></button>
            <button type="button" className="ctlBtn" onClick={remove} disabled={visible.length <= 1}
              title={t("Remove this tool from the strip")} aria-label={t("Remove tool")}><TrashIcon size={16} /></button>
            <button type="button" className="ctlBtn" {...menus.trigger("tool")} title={t("Move, hide, reset or remove this tool")}
              aria-label={t("Tool menu")}><MoreIcon size={16} /></button>
          </>)}
        </div>
      ) : null}
      {options === true && active === "eraser" ? (
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
          <span className="pdfInkSep" />
          <span className="inkErases" role="group" aria-label={t("Erases")}>
            {ERASES.map(([value, label, Icon, title]) => seg(eraserFilter === value, t(label), Icon ? <Icon size={14} /> : null,
              () => onEraser({ filter: value }), t(title)))}
          </span>
          <span className="pdfInkSep" />
          {seg(eraserReturn, t("Back to last tool"), <UndoIcon size={14} />, () => onEraser({ return: !eraserReturn }),
            t("After erasing, return to the tool you had"))}
        </div>
      ) : null}
      {options === true && active === "select" ? (
        <div className="pdfInkSub" data-ink-options="select">
          {seg(lassoMode !== "box", t("Freeform"), <LassoIcon size={14} />, () => onLasso("free"), t("Freeform: draw a loop around the strokes"))}
          {seg(lassoMode === "box", t("Box"), <RectSelectIcon size={14} />, () => onLasso("box"), t("Box: drag a rectangle over the strokes"))}
        </div>
      ) : null}
      {options === true && active === "text" ? (
        // A press here keeps the focus in the box being typed in, which the row restyles too.
        <div className="pdfInkSub" data-ink-options="text" onMouseDown={keepFocus}>
          <TextStyleChoices kind="size" style={textStyle} onStyle={onTextStyle} />
          <span className="pdfInkSep" />
          <TextStyleChoices kind="color" style={textStyle} onStyle={onTextStyle} />
          <span className="pdfInkSep" />
          <TextStyleChoices kind="bg" style={textStyle} onStyle={onTextStyle} />
        </div>
      ) : null}
      {options === true && active === "shape" ? (
        <div className="pdfInkSub" data-ink-options="shape">
          <span className="inkShapeKinds" role="group" aria-label={t("Shape")}>
            {SHAPE_CHOICES.map(([kind, label, Icon, title]) => seg(shapeKind === kind, t(label), <Icon size={14} />,
              () => onShape?.({ kind }), t(title)))}
          </span>
          {stylusPen ? <>
            <span className="pdfInkSep" />
            <span className="inkShapeUses" title={t("Shapes take the colour, width and line of the pen a stylus writes with")}>
              {t("Uses: {color} pen · {size} pt", { color: inkColorName(stylusPen.color), size: stylusPen.size })}
            </span>
          </> : null}
          <span className="pdfInkSep" />
          {seg(!!shapeFill, t("Fill"), <PaintBucketIcon size={14} />, () => onShape?.({ fill: !shapeFill }),
            t("Fill: rectangles and ellipses get a light tint of the pen's colour"))}
        </div>
      ) : null}
      {options === "ruler" && ruler ? (
        <div className="pdfInkSub" data-ink-options="ruler">
          <span className="inkRulerAngle">{t("Angle {deg}°", { deg: Math.round(ruler.angle) % 180 })}</span>
          <span className="inkRulerRotation" role="group" aria-label={t("Ruler rotation")}>
            {seg(!!ruler.snap, t("Snap 15°"), null, () => onRulerOptions?.({ snap: true }), t("Snap 15°: the ruler turns in 15° steps"))}
            {seg(!ruler.snap, t("Free"), null, () => onRulerOptions?.({ snap: false }), t("Free: the ruler turns smoothly; hold Shift to snap"))}
          </span>
          <span className="pdfInkSep" />
          <button type="button" className="ctlBtn inkSegBtn" onClick={() => onStampLine?.()} aria-label={t("Stamp line")}
            title={t("Stamp line: a straight line along the ruler with the armed pen")}><StampLineIcon size={14} /><span>{t("Stamp line")}</span></button>
          <span className="pdfInkSep" />
          {/* unit symbols, the same in every language */}
          <span className="inkRulerUnits" role="group" aria-label={t("Ruler units")}>
            {seg(rulerUnits !== "cm", "pt", null, () => onRulerOptions?.({ units: "pt" }), t("Ticks in points"))}
            {seg(rulerUnits === "cm", "cm", null, () => onRulerOptions?.({ units: "cm" }), t("Ticks in centimetres"))}
          </span>
          <span className="pdfInkSep" />
          <button type="button" className="ctlBtn inkSegBtn" onClick={() => onRulerOptions?.({ hide: true })} aria-label={t("Hide")}
            title={t("Hide the ruler (R)")}><EyeOffIcon size={14} /><span>{t("Hide")}</span></button>
        </div>
      ) : null}
    </InkTooltips>
    {/* The menus, outside the InkTooltips host (see `menus` above). */}
    {menus.kind === "more" ? (
      <ContextMenu {...menus.menu} className="inkStripMenu">
        {menuRow("ink.paste", { disabledTitle: t("Copy or cut handwriting first") })}
        {menuRow("ink.selectAll")}
        <MenuDivider />
        {modifierRow(RulerIcon, t("Ruler"), "R", !!ruler, onToggleRuler)}
        <MenuItem icon={StampLineIcon} disabled={!ruler} title={ruler ? undefined : t("Show the ruler first")} onMouseDown={keepFocus}
          onClick={() => { closeMenu(); onStampLine?.(); }}>{t("Stamp line")}</MenuItem>
        {modifierRow(ShapesIcon, t("Shapes"), "S", active === "shape", onToggleShape)}
        {onToggleZoom && !phone ? modifierRow(ZoomWindowIcon, t("Zoom window"), "Z", !!zoomOpen, onToggleZoom,
          { disabled: !zoomAvailable, title: zoomAvailable ? undefined : t("The zoom window works on PDF pages") }) : null}
        <MenuDivider />
        <MenuItem icon={SettingsIcon} onMouseDown={keepFocus} onClick={() => { closeMenu(); onOpenSettings?.(); }}>
          {t("Handwriting settings…")}
        </MenuItem>
      </ContextMenu>
    ) : null}
    {menus.kind === "style" && preset?.kind === "pen" ? (
      <InkStyleMenu menu={menus.menu} value={brushValue(preset.brush)} onPick={(brush) => edit({ brush })} />
    ) : null}
    {menus.kind === "tool" && preset ? (
      <ContextMenu {...menus.menu} className="inkStripMenu">
        <MenuItem icon={ChevronLeftIcon} disabled={visibleIndex <= 0} onMouseDown={keepFocus} onClick={() => { closeMenu(); move(-1); }}>{t("Move left")}</MenuItem>
        <MenuItem icon={ChevronRightIcon} disabled={visibleIndex < 0 || visibleIndex >= visible.length - 1} onMouseDown={keepFocus}
          onClick={() => { closeMenu(); move(1); }}>{t("Move right")}</MenuItem>
        <MenuItem icon={DuplicateIcon} disabled={tools.length >= MAX_TOOLS} onMouseDown={keepFocus} onClick={() => { closeMenu(); duplicate(); }}>{t("Duplicate")}</MenuItem>
        <MenuItem icon={EyeOffIcon} disabled={visible.length <= 1} onMouseDown={keepFocus} onClick={() => { closeMenu(); hide(); }}>{t("Hide")}</MenuItem>
        <SubMenuItem id="hidden" icon={EyeIcon} label={t("Hidden tools")} disabled={!hidden.length} title={hidden.length ? undefined : t("No hidden tools")}
          onMouseDown={keepFocus}>
          {hidden.map((h) => (
            <MenuItem key={h.id} icon={h.kind === "highlighter" ? HighlightIcon : PenIcon} onMouseDown={keepFocus}
              onClick={() => { closeMenu(); show(h.id); }}>{toolLabel(h)}</MenuItem>
          ))}
        </SubMenuItem>
        <MenuItem icon={TrashIcon} danger disabled={visible.length <= 1} onMouseDown={keepFocus} onClick={() => { closeMenu(); remove(); }}>{t("Remove")}</MenuItem>
        <MenuDivider />
        <MenuItem icon={HistoryIcon} onMouseDown={keepFocus} onClick={() => { closeMenu(); reset(); }}>{t("Reset this tool")}</MenuItem>
      </ContextMenu>
    ) : null}
    {/* The dragged preset's ghost rides with the pointer; on the body, out of the strip's transform. */}
    {drag ? createPortal(
      <div className="inkDragGhost" style={{ left: drag.x, top: drag.y }} aria-hidden="true">
        {(() => {
          const tt = tools.find((x) => x.id === drag.id);
          if (!tt) return null;
          const hl = tt.kind === "highlighter";
          return <>{hl ? <HighlightIcon size={16} /> : <PenIcon size={16} />}<span className="inkToolInk" style={{ background: tt.color, height: 3, opacity: hl ? 0.85 : 1 }} /></>;
        })()}
      </div>, document.body) : null}
  </>;
}
