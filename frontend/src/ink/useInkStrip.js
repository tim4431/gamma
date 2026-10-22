// The tool strip's state (docs/dev/handwriting.md "Client"): whether the
// strip is open, the armed tool, its options row and the stylus's pen;
// the presets as the strip shows them and the armed tool as the layers
// draw with it; and the tool to go back to. App composes it above the
// text boxes, which an armed ink tool makes inert. The rest of the page
// changes the strip only through the commands at the end: each makes one
// fixed update, so each is the same function for the page's life.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { normalizeTools, toolStyle, visibleTools } from "./ink.js";
import { RESERVED_TOOLS, backTool, stylusPen } from "./pencilActions.js";

// focusedBlockId: the open page; leaving it disarms the tool. readOnly,
// and inkStale (the build fence, buildFence.js): no tool that draws is
// armed. inkTools / setInkToolsRaw: the account's preset list and its raw
// writer (app/prefs.js); inkAutoPen, inkShapeKind, inkShapeFill and
// inkEraserReturn: the account's preferences the strip follows.
export function useInkStrip({ focusedBlockId, readOnly, inkStale, inkTools, setInkToolsRaw,
  inkAutoPen, inkShapeKind, inkShapeFill, inkEraserReturn }) {
  // The strip: open, the armed tool (a preset id from inkTools, "eraser",
  // "select", "text" for text boxes, "shape" for the Shapes tool, or null
  // for the hand), which options row is open (false, true for the armed
  // tool's, "ruler" for the ruler's), the pen preset a stylus writes with
  // (the last pen armed).
  const [inkUi, setInkUi] = useState({ open: false, tool: null, options: false, pen: null });
  // Every write of the preset list is normalised (ink.js normalizeTools), so
  // the list this browser keeps and pushes is the one the profile's pull
  // reads back through the same function: a raw patch ({brush: "pen"}, a
  // default opacity) would differ from its own read on the next pull and
  // pass for another device's change. The updater form is the pill's Show.
  const setInkTools = useCallback((next) => setInkToolsRaw((was) => normalizeTools(typeof next === "function" ? next(was) : next)),
    [setInkToolsRaw]);
  // The presets on the strip: the list without its hidden ones (ink.js
  // visibleTools) — what the row, the key table, the stylus's pen and its
  // backTool read; a hidden preset is out of all of them.
  const inkVisible = useMemo(() => visibleTools(inkTools), [inkTools]);
  // The pen preset a stylus writes with when nothing is armed: the last pen
  // armed on the strip, else the first pen in the row (pencilActions.js
  // stylusPen, the rule the Pencil's gestures and backTool read too).
  const inkPenPreset = useMemo(() => stylusPen(inkVisible, inkUi.pen), [inkVisible, inkUi.pen]);
  const inkPen = useMemo(() => toolStyle(inkPenPreset), [inkPenPreset]);
  // What the Shapes tool draws with: the stylus pen's colour, width,
  // opacity and line as an even Ballpoint line (a shape is constant width
  // whatever the style); its smoothing, straightening and nib stay behind.
  const inkShapeStyle = useMemo(() => {
    const { stabilize: _s, straight: _st, nib: _n, nibLock: _l, brush: _b, ...look } = inkPen;
    return look.tool === "pen" ? { ...look, brush: "monoline" } : look;
  }, [inkPen]);
  const inkTool = useMemo(() => {
    const t = inkUi.tool;
    if (!t || readOnly || inkStale) return null;
    if (t === "eraser" || t === "select") return { tool: t };
    if (t === "shape") return { tool: "shape", kind: inkShapeKind, fill: inkShapeFill, style: inkShapeStyle };
    const p = inkTools.find((x) => x.id === t);
    return p ? toolStyle(p) : null;
  }, [inkUi.tool, readOnly, inkTools, inkStale, inkShapeKind, inkShapeFill, inkShapeStyle]);
  // The Text tool (docs/dev/text_boxes.md) takes every pointer, a stylus's too.
  const textArmed = inkUi.tool === "text" && !readOnly;
  const inkPenTool = inkAutoPen && !readOnly && !textArmed && !inkStale ? inkPen : null;
  // Arm a tool; a pen preset also becomes the stylus pen. The options row
  // closes unless the caller keeps it (a duplicate stays editable) or opens
  // it (`open`: the Shapes tool armed from More or S).
  const pickInkTool = useCallback((id, { keepOptions = false, open = false, kind } = {}) => {
    setInkUi((s) => {
      const k = kind || (id && inkTools.find((t) => t.id === id)?.kind);
      return { ...s, open: true, tool: id, options: open && !!id ? true : keepOptions && !!id ? s.options : false, pen: k === "pen" ? id : s.pen };
    });
  }, [inkTools]);
  // Opening the strip arms the stylus pen (stylusPen: the last pen armed,
  // else the first pen on the row), never a highlighter put before it.
  const openInkStrip = () => pickInkTool(stylusPen(inkVisible, inkUi.pen).id);
  // A removed or hidden preset leaves the strip's hand armed.
  useEffect(() => {
    if (inkUi.tool && !RESERVED_TOOLS.includes(inkUi.tool) && !inkVisible.some((t) => t.id === inkUi.tool)) {
      setInkUi((s) => ({ ...s, tool: null, options: false }));
    }
  }, [inkVisible, inkUi.tool]);
  // The tool armed before the current one, written after each commit: an
  // inkBack() in the same event as a pick reads the tool before it.
  const inkPrevRef = useRef({ tool: null, previous: null });
  useEffect(() => {
    const r = inkPrevRef.current;
    if (inkUi.tool === r.tool) return;
    if (r.tool) r.previous = r.tool;
    r.tool = inkUi.tool;
  }, [inkUi.tool]);
  // The tool to go back to: the one armed before the current one, else the
  // stylus pen (the Pencil's switchPrevious picks the same).
  const inkBack = () => backTool({ tools: inkVisible, pen: inkUi.pen }, { tool: inkUi.tool, previous: inkPrevRef.current.previous });
  // The Shapes tool (More → Shapes, S): armed with its row open, or the
  // tool armed before it given back.
  function toggleInkShape() {
    if (inkUi.tool === "shape") pickInkTool(inkBack());
    else pickInkTool("shape", { open: true });
  }
  // The eraser tool lifted, once App's handleInkEraseEnd has told a held E
  // and a held squeeze: with "Eraser returns to the last tool" on, back to
  // the tool armed before it, the options row closed.
  function inkEraserLifted() {
    if (!inkEraserReturn || inkUi.tool !== "eraser" || readOnly) return;
    pickInkTool(inkBack());
  }
  // Leaving a page disarms the tool; the strip stays open.
  useEffect(() => () => {
    setInkUi((s) => (s.tool ? { ...s, tool: null } : s));
  }, [focusedBlockId]);
  // The tool armed before the current one, read when asked (the Pencil's
  // gestures).
  const previousTool = () => inkPrevRef.current.previous;
  // A lasso selection shows the strip, its options row closed.
  const revealInkStrip = useCallback(() => setInkUi((s) => ({ ...s, open: true, options: false })), []);
  // The ruler laid opens its row; put away, it takes its row with it.
  const openRulerRow = useCallback(() => setInkUi((s) => ({ ...s, open: true, options: "ruler" })), []);
  const closeRulerRow = useCallback(() => setInkUi((s) => (s.options === "ruler" ? { ...s, options: false } : s)), []);
  // The strip closes (App's closeInkStrip): the tools disarm.
  const closeInkStripUi = useCallback(() => setInkUi((s) => ({ ...s, open: false, tool: null, options: false })), []);
  // An options row: no argument, the armed tool's; "ruler", the ruler's.
  const toggleInkOptions = useCallback((which) => setInkUi((s) => ({ ...s, options: which === "ruler" ? (s.options === "ruler" ? false : "ruler") : s.options !== true })), []);
  const closeInkOptions = useCallback(() => setInkUi((s) => ({ ...s, options: false })), []);
  // A Pencil gesture's patch of the strip (pencilActions.js {ui}).
  const applyPencilUi = useCallback((ui) => setInkUi((s) => ({ ...s, ...ui })), []);
  // A recording plays: taps seek rather than draw.
  const disarmInk = useCallback(() => setInkUi((s) => (s.tool ? { ...s, tool: null, options: false } : s)), []);
  // The handwriting tour's end (the guide's restore("pen")): the pen last
  // drawn with, when the tour left the eraser, the lasso or Text armed.
  const restorePen = useCallback(() => setInkUi((s) => (s.open && s.pen && RESERVED_TOOLS.includes(s.tool) ? { ...s, tool: s.pen, options: false } : s)), []);
  return {
    inkUi, inkVisible, inkPenPreset, inkPen, inkTool, textArmed, inkPenTool,
    setInkTools, pickInkTool, openInkStrip, toggleInkShape, previousTool, inkEraserLifted,
    revealInkStrip, openRulerRow, closeRulerRow, closeInkStripUi, toggleInkOptions, closeInkOptions,
    applyPencilUi, disarmInk, restorePen,
  };
}
