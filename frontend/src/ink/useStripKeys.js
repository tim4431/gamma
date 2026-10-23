// The strip's keys (docs/dev/handwriting.md "Client"; the key table is
// ink/stripKeys.js, and docs/dev/hotkeys.md "Widget keys" lists the
// keys): while the strip is open, a capture-phase listener on the window
// runs each key through stripKey over the latest render's state and does
// what it answers, and keeps a held E (Hold-E) from its keydown to its
// keyup. App composes it after the zoom window.
import { useCallback, useEffect, useRef } from "react";
import { t } from "../shared/i18n/i18n.js";
import { isTextField } from "../shared/lib/hotkeys.js";
import { inkToolName } from "../markup/MarkupToolbar";
import { stepBrush, stepWidth, stripKey } from "./stripKeys.js";

// inkUi: the strip's state (useInkStrip.js); the listener is on while
// it is open. inkSelection: the lasso's (useInkSelection.js). inkVisible,
// inkTools, setInkTools: the presets on the strip, the account's list
// and its writer. pickInkTool, toggleInkShape, closeInkOptions,
// toggleInkOptions: the strip's commands. closeInkStrip: App's (the
// strip, the ruler, the zoom window). inkUndo: a step of the stroke
// history (useInkDocument.js). boxUndo: the Text tool armed or a box
// selected, whose Ctrl+Z is the block history's; hasBox: a box selected,
// whose Escape is its own. deleteInkSelection, selectionStill,
// clearInkSelection, restoreInkSelection: the selection's. inkRuler,
// toggleInkRuler; inkZoom, toggleInkZoom, inkZoomNewLine: the ruler's
// and the zoom window's (useInkRuler.js, useInkZoom.js). setStatus: the
// status pill. The listener calls clearInkSelection, restoreInkSelection,
// closeInkOptions, toggleInkOptions and setStatus as it found them when
// the strip opened: each is the same function for the page's life.
export function useStripKeys({ inkUi, inkSelection, inkVisible, inkTools, setInkTools, pickInkTool,
  toggleInkShape, closeInkOptions, toggleInkOptions, closeInkStrip, inkUndo, boxUndo, hasBox,
  deleteInkSelection, selectionStill, clearInkSelection, restoreInkSelection,
  inkRuler, toggleInkRuler, inkZoom, toggleInkZoom, inkZoomNewLine, setStatus }) {
  // The strip's keys while it is open (the table is ink/stripKeys.js): 1–9
  // arm the visible preset at that position, P / H step through the pens /
  // highlighters (Shift: backwards), [ / ] step the armed preset's width
  // with a pill, Shift+[ / ] an armed pen's style through BRUSH_ORDER (Pen,
  // Ballpoint, Brush, Pencil, wrapping; the same pill naming the style), E
  // taps or holds the eraser ("Hold-E" below), L the lasso,
  // T the Text tool, V the hand, Esc closes one layer at a time (the
  // selection, the options row, the strip), Delete removes the selection,
  // and Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y step the STROKE history — registered
  // in the capture phase so the page's block undo (a bubble listener on the
  // window) never sees them. Text boxes are blocks: with the Text tool armed
  // or a box selected, Ctrl+Z is left to the block undo, and Esc to the box
  // (it lets go first). A focused text field keeps its own keys, and an open
  // menu or a preset drag keeps Escape (they close themselves).
  // Hold-E: the keydown arms the eraser as a tap does and keeps {since,
  // erased, selection, tool} — the lasso selection and the tool armed before;
  // handleInkEraseEnd marks `erased`; the keyup after 300 ms or more with an
  // erasing pass between picks that tool again and, when it is the lasso,
  // restores what is left of the selection (selectionStill). A shorter
  // press, or one that erased nothing, leaves the eraser armed (a tap with
  // it already armed opens its row, as a tap on the button does). A window
  // blur ends a hold.
  const inkKeysRef = useRef(null);
  inkKeysRef.current = { inkUndo, deleteInkSelection, selectionStill, hasSelection: !!inkSelection, selection: inkSelection, tools: inkVisible,
    allTools: inkTools, setInkTools, tool: inkUi.tool, pickInkTool, boxUndo, hasBox, optionsOpen: !!inkUi.options,
    // the modifiers: R, S, Z, the zoom window's Enter and their places in Esc's order
    ruler: !!inkRuler, toggleInkRuler, toggleInkShape, closeInkStrip,
    zoom: !!inkZoom, toggleInkZoom, inkZoomNewLine };
  const inkHoldRef = useRef(null);
  // The eraser tool lifted (App's handleInkEraseEnd): a held E learns that
  // a pass was made.
  const noteInkEraseHold = useCallback(() => { if (inkHoldRef.current) inkHoldRef.current.erased = true; }, []);
  useEffect(() => {
    if (!inkUi.open) return;
    const endHold = () => { inkHoldRef.current = null; };
    const onKey = (e) => {
      // A text field keeps its keys; a keyup is harmless and ends a hold
      // whose E is let go after a click into a caption.
      if (e.type !== "keyup" && isTextField(e.target)) return;
      const K = inkKeysRef.current;
      // a focused button, link, menu row or comment thread (a margin card or the
      // thread popover, whose Enter opens it) keeps its Enter
      const onControl = !!e.target?.closest?.("button, a[href], [role^='menuitem'], .commentMargin, .commentPopover");   // i18n-ignore: a selector
      const action = stripKey(e, { ...K, onControl, eraserDown: inkHoldRef.current?.since ?? null });
      if (!action) return;
      // an open menu (not one on its way out, shared/ui/leave.js), a preset
      // being dragged and a focused comment thread take Escape first
      if (e.code === "Escape" && (document.querySelector(".ctxMenu:not([data-leaving]), .inkDragGhost")
        || e.target?.closest?.(".commentMargin, .commentPopover"))) return;   // i18n-ignore: a selector
      if (action.prevent) e.preventDefault();
      if (action.stop) e.stopPropagation();
      if (action.kind === "undo") K.inkUndo(action.redo);
      else if (action.kind === "deselect") clearInkSelection();
      else if (action.kind === "closeOptions") closeInkOptions();
      else if (action.kind === "close") K.closeInkStrip();
      else if (action.kind === "toggle" || action.kind === "off") {
        // R / S / Z, and Esc taking the Shapes tool, the ruler or the zoom
        // window away: stripKey says "off" only for one that is on
        if (action.what === "ruler") K.toggleInkRuler();
        else if (action.what === "shape") K.toggleInkShape();
        else if (action.what === "zoom") K.toggleInkZoom();
      } else if (action.kind === "newLine") K.inkZoomNewLine();
      else if (action.kind === "delete") K.deleteInkSelection();
      else if (action.kind === "pick") K.pickInkTool(action.id);
      else if (action.kind === "width") {
        const stepped = stepWidth(K.allTools, K.tool, action.step);
        if (!stepped) return;
        if (stepped.next) K.setInkTools(stepped.next);
        setStatus(t("{name} · {size} pt.", { name: inkToolName(stepped.preset), size: stepped.size }));
      } else if (action.kind === "brush") {
        const stepped = stepBrush(K.allTools, K.tool, action.step);
        if (!stepped) return;   // a highlighter has no styles
        K.setInkTools(stepped.next);
        setStatus(t("{name} · {size} pt.", { name: inkToolName(stepped.preset), size: stepped.size }));
      } else if (action.kind === "eraser" && action.phase === "down") {
        if (inkHoldRef.current) return;
        inkHoldRef.current = { since: e.timeStamp, erased: false, selection: K.selection, tool: K.tool };
        if (K.tool !== "eraser") K.pickInkTool("eraser");
      } else if (action.kind === "eraser") {
        const hold = inkHoldRef.current;
        inkHoldRef.current = null;
        if (!hold) return;
        if (action.held >= 300 && hold.erased) {
          if (hold.tool === "eraser") return;
          K.pickInkTool(hold.tool);
          if (hold.tool === "select" && hold.selection) restoreInkSelection(K.selectionStill(hold.selection));
        } else if (hold.tool === "eraser") toggleInkOptions();
      }
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("keyup", onKey, true);
    window.addEventListener("blur", endHold);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("keyup", onKey, true);
      window.removeEventListener("blur", endHold);
      endHold();
    };
  }, [inkUi.open]);
  return { noteInkEraseHold };
}
