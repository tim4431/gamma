// The tool strip's key table (docs/dev/handwriting.md "What the user
// sees"), pure: a key event → the action the handler in useStripKeys.js
// dispatches, or null when the key is not the strip's or is left to
// another owner (the block history, a selected text box). Keys are matched
// by `e.code`, so a layout that puts another letter on the E key still arms
// the eraser there, and Shift never changes which key it was. Actions:
// {kind: "undo", redo} · {kind: "deselect"} · {kind: "closeOptions"} ·
// {kind: "close"} · {kind: "delete"} · {kind: "pick", id} (a preset id,
// "select", "text", or null for the hand) · {kind: "width", step: ±1} (the
// armed preset thinner or thicker) · {kind: "brush", step: ±1} (the same
// keys with Shift: the armed pen's style back or forward through Pen,
// Ballpoint, Brush, Pencil, Calligraphy) · {kind: "eraser", phase: "down" | "up",
// held} (E: `down` on a non-repeat keydown, `up` on the keyup with `held`
// the ms since the down, so the handler decides tap or hold) · {kind: "toggle",
// what: "ruler" | "shape" | "zoom"} (R, S, Z: the ruler, the Shapes tool,
// the zoom window on or off) · {kind: "off", what} (Escape taking one of
// them away) · {kind: "newLine"} (Enter while the zoom window is open: its
// New line) · {kind: "picture"} (I: the Picture button, which opens the
// file picker; P is the pens'); `prevent` and `stop` say what to do with
// the event.
// stepWidth and stepBrush, below, are what width and brush do to the
// armed preset.
//
// A chord with Ctrl, Cmd or Alt is someone else's (Ctrl+C / X / V / D / A
// on handwriting are catalog commands, app/appCommands.js), except Ctrl+Z /
// Y and the AltGr pair — ctrlKey and altKey together, a third-level letter
// on some layouts — which counts as no modifier.
import { BRUSH_ORDER, sizesFor } from "./ink.js";

// state: {tools: the visible presets in row order, tool: the armed tool,
// hasSelection: the lasso holds strokes, boxUndo: Ctrl+Z belongs to the
// block history (the Text tool is armed, a box is selected or edited, or a
// picture is selected), hasBox: a text box or a picture is selected
// (Escape is its), optionsOpen: an options row is open, the armed tool's
// or the ruler's (Escape closes it before the strip), eraserDown: the
// timeStamp of the E keydown still held, or null, ruler / zoom: the ruler
// shows / the zoom window is open, onControl: the key's target is a
// button, a link or a menu row (which keeps its Enter)}. Escape goes one
// layer at a time: the text box or the picture, the selection, the open
// row, the Shapes tool (the tool before it comes back), the ruler, the
// zoom window, then the strip.
export function stripKey(e, state = {}) {
  const { tools = [], tool = null, hasSelection = false, boxUndo = false, hasBox = false, optionsOpen = false, eraserDown = null,
    ruler = false, zoom = false, onControl = false } = state;
  const code = e.code || "";
  if (e.type === "keyup") {
    if (code !== "KeyE" || eraserDown == null) return null;
    return { kind: "eraser", phase: "up", held: Math.max(0, (e.timeStamp || 0) - eraserDown) };
  }
  const altGr = !!(e.ctrlKey && e.altKey);
  const mod = (e.ctrlKey || e.metaKey) && !altGr;
  const alt = e.altKey && !altGr;
  if (mod && !e.altKey && (code === "KeyZ" || code === "KeyY")) {
    if (boxUndo) return null;
    return { kind: "undo", redo: code === "KeyY" || !!e.shiftKey, prevent: true, stop: true };
  }
  if (code === "Escape") {
    if (hasBox) return null;
    if (hasSelection) return { kind: "deselect" };
    if (optionsOpen) return { kind: "closeOptions" };
    if (tool === "shape") return { kind: "off", what: "shape" };
    if (ruler) return { kind: "off", what: "ruler" };
    if (zoom) return { kind: "off", what: "zoom" };
    return { kind: "close" };
  }
  if ((code === "Delete" || code === "Backspace") && hasSelection) return { kind: "delete", prevent: true };
  if (mod || alt) return null;
  if (code === "KeyE") return e.repeat ? null : { kind: "eraser", phase: "down" };
  if (code === "KeyL") return { kind: "pick", id: "select" };
  if (code === "KeyT") return { kind: "pick", id: "text" };
  if (code === "KeyV") return { kind: "pick", id: null };
  if (code === "KeyR") return { kind: "toggle", what: "ruler" };
  if (code === "KeyS") return { kind: "toggle", what: "shape" };
  if (code === "KeyZ") return { kind: "toggle", what: "zoom" };
  if (code === "KeyI") return { kind: "picture" };
  // a selected text box keeps its Enter (it opens the box), as it keeps its Escape
  if ((code === "Enter" || code === "NumpadEnter") && zoom && !onControl && !hasBox) return { kind: "newLine", prevent: true };
  if (code === "BracketLeft" || code === "BracketRight") {
    return { kind: e.shiftKey ? "brush" : "width", step: code === "BracketLeft" ? -1 : 1 };
  }
  if (code === "KeyP" || code === "KeyH") {
    // The next preset of that kind after the armed one, wrapping; with
    // Shift the one before it.
    const kind = code === "KeyP" ? "pen" : "highlighter";
    const list = tools.filter((t) => t.kind === kind);
    if (!list.length) return null;
    const i = list.findIndex((t) => t.id === tool);
    const next = e.shiftKey ? (i < 0 ? list.length - 1 : (i - 1 + list.length) % list.length) : (i + 1) % list.length;
    return { kind: "pick", id: list[next].id };
  }
  const digit = /^(?:Digit|Numpad)([1-9])$/.exec(code);
  if (digit) {
    const t = tools[Number(digit[1]) - 1];
    return t ? { kind: "pick", id: t.id } : null;
  }
  return null;
}

// The width keys' step for the armed preset `id` of `tools`, the
// account's list: the next size on its kind's ladder (ink.js sizesFor; a
// size off the ladder steps from its first), `step` ±1. → {preset, size,
// next}: the preset and the size the pill names ("{name} · {size} pt.",
// raised at either end of the ladder too), and the list with the preset
// at that size, null when the size stays (at an end). null with no preset
// armed.
export function stepWidth(tools, id, step) {
  const p = tools.find((x) => x.id === id);
  if (!p) return null;
  const sizes = sizesFor(p.kind);
  const size = sizes[Math.max(0, Math.min(sizes.length - 1, sizes.indexOf(p.size) + step))];
  return { preset: p, size, next: size !== p.size ? tools.map((x) => (x.id === p.id ? { ...x, size } : x)) : null };
}

// The style keys' step for an armed pen: its brush through BRUSH_ORDER
// (null for Pen, then Ballpoint, Brush, Pencil, Calligraphy), wrapping,
// the brush alone written (a calligraphy pen's smoothing of 2 is its
// default, ink.js presetSmoothing, never written). → {preset: the pen
// restyled, which the pill names, size, next: the list with it}; null for
// a highlighter, which has no styles, or with no preset armed.
export function stepBrush(tools, id, step) {
  const p = tools.find((x) => x.id === id);
  if (!p || p.kind !== "pen") return null;
  const n = BRUSH_ORDER.length, at = Math.max(0, BRUSH_ORDER.indexOf(p.brush ?? null));
  const brush = BRUSH_ORDER[(at + step + n) % n];
  const { brush: _was, ...rest } = p;
  const preset = brush ? { ...rest, brush } : rest;
  return { preset, size: p.size, next: tools.map((x) => (x.id === p.id ? preset : x)) };
}
