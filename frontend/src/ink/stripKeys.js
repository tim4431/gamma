// The tool strip's key table (docs/dev/handwriting.md "What the user
// sees"), pure: a key event → the action App's capture-phase handler
// dispatches, or null when the key is not the strip's or is left to
// another owner (the block history, a selected text box). Keys are matched
// by `e.code`, so a layout that puts another letter on the E key still arms
// the eraser there, and Shift never changes which key it was. Actions:
// {kind: "undo", redo} · {kind: "deselect"} · {kind: "closeOptions"} ·
// {kind: "close"} · {kind: "delete"} · {kind: "pick", id} (a preset id,
// "select", "text", or null for the hand) · {kind: "width", step: ±1} (the
// armed preset thinner or thicker) · {kind: "eraser", phase: "down" | "up",
// held} (E: `down` on a non-repeat keydown, `up` on the keyup with `held`
// the ms since the down, so App decides tap or hold); `prevent` and `stop`
// say what to do with the event.
//
// A chord with Ctrl, Cmd or Alt is someone else's (Ctrl+C / X / V / D / A
// on handwriting are catalog commands, app/appCommands.js), except Ctrl+Z /
// Y and the AltGr pair — ctrlKey and altKey together, a third-level letter
// on some layouts — which counts as no modifier.

// state: {tools: the visible presets in row order, tool: the armed tool,
// hasSelection: the lasso holds strokes, boxUndo: Ctrl+Z belongs to the
// block history (the Text tool is armed or a box is selected or edited),
// hasBox: a text box is selected (Escape is its), optionsOpen: the armed
// tool's options row is open (Escape closes it before the strip),
// eraserDown: the timeStamp of the E keydown still held, or null}.
export function stripKey(e, state = {}) {
  const { tools = [], tool = null, hasSelection = false, boxUndo = false, hasBox = false, optionsOpen = false, eraserDown = null } = state;
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
    return { kind: hasSelection ? "deselect" : optionsOpen ? "closeOptions" : "close" };
  }
  if ((code === "Delete" || code === "Backspace") && hasSelection) return { kind: "delete", prevent: true };
  if (mod || alt) return null;
  if (code === "KeyE") return e.repeat ? null : { kind: "eraser", phase: "down" };
  if (code === "KeyL") return { kind: "pick", id: "select" };
  if (code === "KeyT") return { kind: "pick", id: "text" };
  if (code === "KeyV") return { kind: "pick", id: null };
  if (code === "BracketLeft" || code === "BracketRight") return { kind: "width", step: code === "BracketLeft" ? -1 : 1 };
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
