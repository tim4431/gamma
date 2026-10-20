// The Pencil's double tap and squeeze, as the iPad app's shell forwards
// them with the system's preferred action (shared/lib/host.js "pencil",
// docs/dev/ipad.md): what the strip does for each. Pure; App applies the
// answer. `message` is the host's {action} (or the action string):
// switchEraser toggles the eraser against the tool before it,
// switchPrevious goes back to that tool, showColorPalette and
// showInkAttributes open the armed tool's options row. prefs: {tools: the
// preset list, pen: the id of the pen a stylus writes with}; state: {tool:
// the armed tool, previous: the tool armed before it, options: the options
// row is open}. → {pick: id} (arm that tool; null is the hand),
// {ui: {open, tool?, options}} (a patch of the strip's state), or null.

// The tool to go back to: the one armed before the current one when it is
// still on the strip and not the current one, else the pen a stylus writes
// with (the last pen armed, else the first preset). The Pencil's
// switchPrevious and the eraser's return to the last tool (App's inkBack)
// both pick it.
export function backTool(prefs = {}, state = {}) {
  const tools = prefs.tools || [];
  const pen = tools.find((x) => x.id === prefs.pen)?.id || tools[0]?.id || null;
  const { tool = null, previous = null } = state;
  const known = ["eraser", "select", "text"].includes(previous) || tools.some((x) => x.id === previous);
  return known && previous !== tool ? previous : pen;
}

export function pencilAction(message, prefs = {}, state = {}) {
  const action = typeof message === "string" ? message : message?.action;
  const tools = prefs.tools || [];
  const pen = tools.find((x) => x.id === prefs.pen)?.id || tools[0]?.id || null;
  const { tool = null, options = false } = state;
  const back = backTool(prefs, state);
  if (action === "switchEraser") return { pick: tool === "eraser" ? back : "eraser" };
  if (action === "switchPrevious") return { pick: back };
  if (action === "showColorPalette" || action === "showInkAttributes") {
    // With the hand armed the Pencil writes with the stylus pen: its row.
    return { ui: tool ? { open: true, options: !options } : { open: true, tool: pen, options: true } };
  }
  return null;
}
