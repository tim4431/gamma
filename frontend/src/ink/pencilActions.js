// The Pencil's double tap and squeeze, as the iPad app's shell forwards them
// (docs/dev/ipad.md "Gestures"): what the strip does for each, by this
// browser's choice for the gesture. Pure; App applies the answer.
//
// message: {gesture: 'tap' | 'squeeze', action, phase?: 'began' | 'ended' |
// 'cancelled'} — the host's `pencil` (a double tap, or a squeeze's end) and
// its `squeeze` (a squeeze's start, or its cancel), `action` the system's
// preferred action; a bare action string is a double tap. A squeeze in a
// phase this page does not know (a later shell's) does nothing.
// prefs: {tools: the preset list, pen: the id of the pen a stylus writes
// with, tap, squeeze: this browser's inkPencilTap and inkPencilSqueeze}.
// state: {tool: the armed tool, previous: the tool armed before it,
// options: the open options row, App's inkUi.options — true for the armed
// tool's, "ruler" for the ruler's (not the tool's: Show options opens the
// tool's in its place), false for none —, busy: a stroke or a claim is in
// progress (InkLayer.jsx inkBusy), squeeze: the squeeze App holds open,
// {since, ignored, hold: {tool, used} | null} or null, now: ms on the clock
// `since` was read from}.
// → {pick: id} (arm that tool; null is the hand), {ui: {open, tool?,
// options}} (a patch of the strip's state), {undo: true} (what the strip's
// Undo does), {began: {ignored, pick?}} (a squeeze started; `pick`, for a
// hold choice, the tool to arm while it is held: what the choice's toggle
// would arm, so with the eraser armed already Eraser while held holds the
// tool to go back to, and a short squeeze gives it back), {release: {pick} |
// {stay: true}} (a held squeeze ended or was cancelled: back to `pick`, the
// tool armed when it began, or, after a squeeze under SQUEEZE_TAP_MS that
// neither erased nor drew, the tool it armed stays), or null.

// What a gesture can be set to (prefDefs.js inkPencilTap, inkPencilSqueeze):
// 'system' follows the system's preferred action; the squeeze also has the
// two hold choices, a tool armed only while the Pencil is squeezed.
export const PENCIL_CHOICES = ["system", "eraser", "previous", "options", "undo", "none"];
export const SQUEEZE_HOLDS = ["holdEraser", "holdPrevious"];
// A held squeeze shorter than this that neither erased nor drew was a
// toggle: the tool it armed stays armed.
export const SQUEEZE_TAP_MS = 300;
// The phases of a squeeze (the host's `squeeze` and `pencil`).
const PHASES = ["began", "ended", "cancelled"];
// The system's preferred actions (UIPencilPreferredAction) as the choices
// they stand for under 'system'; any other (runSystemShortcut, ignore) is
// nothing.
const SYSTEM = { switchEraser: "eraser", switchPrevious: "previous", showColorPalette: "options", showInkAttributes: "options" };

// The pen a stylus writes with: the last pen armed (prefs.pen) while it is
// on the strip, else the first preset; null for none.
const stylusPen = (prefs) => {
  const tools = prefs.tools || [];
  return tools.find((x) => x.id === prefs.pen)?.id || tools[0]?.id || null;
};

// The strip's tools that are no preset; App reads the list too.
export const RESERVED_TOOLS = ["eraser", "select", "text", "shape"];

// The tool to go back to: the one armed before the current one when it is
// still on the strip and not the current one, else the pen a stylus writes
// with (stylusPen). The gestures' Switch to last tool, the eraser's return
// to the last tool and the Shapes tool's off (App's inkBack) all pick it.
// The reserved tools count as on the strip.
export function backTool(prefs = {}, state = {}) {
  const { tool = null, previous = null } = state;
  const known = RESERVED_TOOLS.includes(previous) || (prefs.tools || []).some((x) => x.id === previous);
  return known && previous !== tool ? previous : stylusPen(prefs);
}

// The choice in effect for a gesture: this browser's, or under 'system'
// (the default, and whatever else is stored) the system's action's.
function choiceOf(gesture, action, prefs) {
  const own = gesture === "squeeze" ? prefs.squeeze : prefs.tap;
  const known = PENCIL_CHOICES.includes(own) || (gesture === "squeeze" && SQUEEZE_HOLDS.includes(own));
  const choice = known ? own : "system";
  return choice === "system" ? SYSTEM[action] || "none" : choice;
}

// What a choice does at once; a hold choice acts as its toggle.
function act(choice, prefs, state) {
  const { tool = null, options = false } = state;
  switch (choice) {
    case "eraser":
    case "holdEraser":
      return { pick: tool === "eraser" ? backTool(prefs, state) : "eraser" };
    case "previous":
    case "holdPrevious":
      return { pick: backTool(prefs, state) };
    case "options":
      // With the hand armed the Pencil writes with the stylus pen: its row.
      return { ui: tool ? { open: true, options: options !== true } : { open: true, tool: stylusPen(prefs), options: true } };
    case "undo":
      return { undo: true };
    default:
      return null;
  }
}

export function pencilAction(message, prefs = {}, state = {}) {
  const msg = typeof message === "string" ? { action: message } : message || {};
  const gesture = msg.gesture === "squeeze" ? "squeeze" : "tap";
  // a double tap has no phases
  const phase = gesture === "squeeze" ? msg.phase : undefined;
  // a phase this page does not know (a later shell's `changed`) is nothing
  if (phase != null && !PHASES.includes(phase)) return null;
  const choice = choiceOf(gesture, msg.action, prefs);
  const { busy = false, squeeze = null, now = 0 } = state;
  // A squeeze's start answers for every choice, so its end can tell what
  // it began; one that comes while a stroke is in progress is ignored, its
  // end with it.
  if (phase === "began") {
    if (busy) return { began: { ignored: true } };
    if (!SQUEEZE_HOLDS.includes(choice)) return { began: { ignored: false } };
    // held, the tool its toggle would arm: the eraser, or with the eraser
    // armed the tool to go back to; the last tool, for Last tool while held
    return { began: { ignored: false, pick: act(choice, prefs, state).pick } };
  }
  if (phase === "ended" || phase === "cancelled") {
    if (!squeeze || squeeze.ignored) return null;
    // a hold's end is never lost, a stroke in progress or not
    if (squeeze.hold) {
      if (now - squeeze.since < SQUEEZE_TAP_MS && !squeeze.hold.used) return { release: { stay: true } };
      return { release: { pick: squeeze.hold.tool ?? null } };
    }
    return phase === "ended" && !busy ? act(choice, prefs, state) : null;
  }
  // No phase: a double tap, or a squeeze from a shell without phases.
  return busy ? null : act(choice, prefs, state);
}
