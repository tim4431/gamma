// The tool strip's key table (docs/dev/handwriting.md "What the user
// sees"), pure: a keydown → the action App's capture-phase handler
// dispatches, or null when the key is not the strip's or is left to
// another owner (the block history, a selected text box). Keys are matched
// by `e.code`, so a layout that puts another letter on the E key still arms
// the eraser there. Actions: {kind: "undo", redo} · {kind: "deselect"} ·
// {kind: "close"} · {kind: "delete"} · {kind: "pick", id} (a preset id,
// "eraser", "select", "text", or null for the hand); `prevent` and `stop`
// say what to do with the event.

// state: {tools: the preset list, tool: the armed tool, hasSelection: the
// lasso holds strokes, boxUndo: Ctrl+Z belongs to the block history (the
// Text tool is armed or a box is selected or edited), hasBox: a text box
// is selected (Escape is its)}.
export function stripKey(e, state = {}) {
  const { tools = [], tool = null, hasSelection = false, boxUndo = false, hasBox = false } = state;
  const code = e.code || "";
  const mod = e.ctrlKey || e.metaKey;
  if (mod && !e.altKey && (code === "KeyZ" || code === "KeyY")) {
    if (boxUndo) return null;
    return { kind: "undo", redo: code === "KeyY" || !!e.shiftKey, prevent: true, stop: true };
  }
  if (code === "Escape") return hasBox ? null : { kind: hasSelection ? "deselect" : "close" };
  if ((code === "Delete" || code === "Backspace") && hasSelection) return { kind: "delete", prevent: true };
  if (mod || e.altKey) return null;
  if (code === "KeyE") return { kind: "pick", id: "eraser" };
  if (code === "KeyL") return { kind: "pick", id: "select" };
  if (code === "KeyT") return { kind: "pick", id: "text" };
  if (code === "KeyV") return { kind: "pick", id: null };
  if (code === "KeyP" || code === "KeyH") {
    // The next preset of that kind after the armed one, wrapping.
    const kind = code === "KeyP" ? "pen" : "highlighter";
    const list = tools.filter((t) => t.kind === kind);
    if (!list.length) return null;
    const i = list.findIndex((t) => t.id === tool);
    return { kind: "pick", id: list[(i + 1) % list.length].id };
  }
  const digit = /^(?:Digit|Numpad)([1-9])$/.exec(code);
  if (digit) {
    const t = tools[Number(digit[1]) - 1];
    return t ? { kind: "pick", id: t.id } : null;
  }
  return null;
}
