// The Apple Pencil under the iPad app (docs/dev/handwriting.md "Client",
// docs/dev/ipad.md "Gestures"; ink/pencilActions.js for the rules): its
// double tap and squeeze as the host forwards them, a held squeeze from
// its start to its end, and the ticks an Apple Pencil Pro plays. App
// composes it after the strip's keys.
import { useCallback, useEffect, useRef } from "react";
import { nativeHost, onHostMessage, postToHost } from "../shared/lib/host.js";
import { inkBusy } from "./InkLayer";
import { round2 } from "./ink.js";
import { pencilAction } from "./pencilActions.js";

// readOnly: no gesture acts. takesInk: the page takes ink (a PDF page or
// the notebook view shown, or sheets among the notes); elsewhere only a
// held squeeze's end acts. inkUi, inkVisible, previousTool(),
// pickInkTool, applyPencilUi: the strip's state, its presets, the tool
// armed before, and its commands (useInkStrip.js). inkSelection,
// selectionStill, restoreInkSelection: the lasso's selection, kept aside
// while a squeeze is held (useInkSelection.js). stripUndo: the strip's
// Undo (useInkDocument.js). inkPencilTap, inkPencilSqueeze, inkHaptics:
// this browser's choices for the double tap and the squeeze, and its
// Haptics.
export function usePencil({ readOnly, takesInk, inkUi, inkVisible, previousTool, pickInkTool, applyPencilUi,
  inkSelection, selectionStill, restoreInkSelection, stripUndo, inkPencilTap, inkPencilSqueeze, inkHaptics }) {
  // The Pencil's double tap and squeeze, forwarded by the iPad app's shell
  // (docs/dev/ipad.md "Gestures"; the host's `pencil`, and `squeeze` for a
  // squeeze's start and cancel): each goes through pencilAction with this
  // browser's choice for the gesture (Settings → Reading & editing › Apple
  // Pencil; the system's preferred action by default) — a tool armed, the
  // armed tool's options row, the strip's Undo — only where a page takes
  // ink, and none while a stroke or a claim is in progress (InkLayer.jsx
  // inkBusy). A held squeeze (squeezeRef: {since, ignored, hold: {tool,
  // used, selection} | null}, from its start to its end or cancel) arms its
  // tool while it is held and goes back to the tool before at its end,
  // restoring what is left of the lasso's selection, unless it was a short
  // one that neither erased nor drew (handleInkEraseEnd and handleInkStroke
  // mark it used). A `squeeze` is a start or a cancel; in any other phase
  // (a later shell's) it does nothing.
  // Each tool a gesture switches to ticks in an Apple Pencil Pro.
  const squeezeRef = useRef(null);
  const pencilRef = useRef(null);
  pencilRef.current = (message) => {
    const ending = message.gesture === "squeeze" && (message.phase === "ended" || message.phase === "cancelled");
    const open = squeezeRef.current;
    if (ending) squeezeRef.current = null;
    // the end of a held squeeze always goes back, wherever the page is now
    if (!(ending && open?.hold) && (readOnly || !takesInk)) return;
    const now = performance.now();
    const result = pencilAction(message, { tools: inkVisible, pen: inkUi.pen, tap: inkPencilTap, squeeze: inkPencilSqueeze },
      { tool: inkUi.tool, previous: previousTool(), options: inkUi.options, busy: inkBusy(), squeeze: open, now });
    if (!result) return;
    // a held squeeze's tool, armed and given back (the same tool is no switch)
    const hold = (id) => {
      if (id === inkUi.tool) return;
      pickInkTool(id);
      hostHaptic("tool");
    };
    if (result.began) {
      const held = "pick" in result.began;
      squeezeRef.current = { since: now, ignored: result.began.ignored,
        hold: held ? { tool: inkUi.tool, used: false, selection: inkSelection } : null };
      if (held) hold(result.began.pick);
    } else if (result.release) {
      if (result.release.stay) return;
      hold(result.release.pick);
      if (result.release.pick === "select" && open?.hold?.selection) restoreInkSelection(selectionStill(open.hold.selection));
    } else if (result.undo) {
      stripUndo(false);
    } else if ("pick" in result) {
      if (result.pick !== inkUi.tool) hostHaptic("tool");
      pickInkTool(result.pick);
    } else applyPencilUi(result.ui);
  };
  useEffect(() => {
    if (!nativeHost()?.pencil) return undefined;
    const offPencil = onHostMessage("pencil", (msg) => { pencilRef.current(msg || {}); });
    const offSqueeze = onHostMessage("squeeze", (msg) => {
      if (msg?.phase === "began" || msg?.phase === "cancelled") pencilRef.current({ ...msg, gesture: "squeeze" });
    });
    return () => { offPencil(); offSqueeze(); };
  }, []);
  // A stroke or an erase pass while a held squeeze is open
  // (useInkStrokes.js handleInkStroke, App's handleInkEraseEnd) marks it
  // used, so its end goes back; the answer is whether one was open.
  const noteSqueezeUse = useCallback(() => { const hold = squeezeRef.current?.hold; if (hold) hold.used = true; return !!hold; }, []);
  // A tick in an Apple Pencil Pro (the host's `haptic`): a gesture's tool
  // switch ('tool'), and from the ink layer a pen lasso that selected
  // ('select') and a held shape ('snap') at a point in viewport css px;
  // only under a host that plays them, with this browser's Haptics on.
  function hostHaptic(kind, at) {
    if (!nativeHost()?.haptics || !inkHaptics) return;
    const point = at && Number.isFinite(at.x) && Number.isFinite(at.y)
      ? { at: { x: round2(at.x), y: round2(at.y) } } : {};
    postToHost({ type: "haptic", kind, ...point }).catch(() => {});
  }
  return { hostHaptic, noteSqueezeUse };
}
