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
export function pencilAction(message, prefs = {}, state = {}) {
  const action = typeof message === "string" ? message : message?.action;
  const tools = prefs.tools || [];
  const pen = tools.find((x) => x.id === prefs.pen)?.id || tools[0]?.id || null;
  const { tool = null, previous = null, options = false } = state;
  const known = ["eraser", "select", "text"].includes(previous) || tools.some((x) => x.id === previous);
  const back = known && previous !== tool ? previous : pen;
  if (action === "switchEraser") return { pick: tool === "eraser" ? back : "eraser" };
  if (action === "switchPrevious") return { pick: back };
  if (action === "showColorPalette" || action === "showInkAttributes") {
    // With the hand armed the Pencil writes with the stylus pen: its row.
    return { ui: tool ? { open: true, options: !options } : { open: true, tool: pen, options: true } };
  }
  return null;
}
