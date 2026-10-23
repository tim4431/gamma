// The ruler (docs/dev/handwriting.md "Client", ink/ruler.js for its
// rules): a straight edge on one surface, session state that goes with
// the strip, the page, and the viewer's showing its surface; its options
// row, Stamp line, and the layers' moves of it. App composes it after the
// selection.
import { useCallback, useEffect, useRef, useState } from "react";
import { t, T } from "../shared/i18n/i18n.js";
import { toolStyle } from "./ink.js";
import { defaultRuler, snapAngle, stampLine } from "./ruler.js";   // the ruler's first place, its Snap 15° and its Stamp line
import { visiblePart } from "./useInkSurfaces.js";

// focusedBlockId: the open page; leaving it takes the ruler away.
// readOnly, and inkStale (the build fence, buildFence.js): no ruler is
// laid, no line stamped. pdfHidden, notebook: the viewer shows no PDF
// page, or the notebook view's sheets. inkUi, inkVisible, inkPen: the
// strip's state, its presets and the stylus pen (useInkStrip.js), whose
// look a stamped line takes. setInkRulerUnits: this browser's ticks
// preference. pasteTarget(): the surface in view (useInkSurfaces.js).
// openRulerRow(), closeRulerRow(): the strip's ruler row.
// handleInkStroke: a stroke into its group (useInkStrokes.js).
// undoableInkStatus(msg): the document's pill with Undo.
export function useInkRuler({ focusedBlockId, readOnly, inkStale, pdfHidden, notebook, inkUi, inkVisible, inkPen,
  setInkRulerUnits, pasteTarget, openRulerRow, closeRulerRow, handleInkStroke, undoableInkStatus }) {
  // The ruler (ink/ruler.js): {surface, x, y, angle, length, snap} in the
  // surface's page points, or null — session state, gone with the strip and
  // with the page — and the size of the surface it was laid on, for Stamp
  // line's group.
  const [inkRuler, setInkRuler] = useState(null);
  const inkRulerSizeRef = useRef(null);
  // The strip closes (App's closeInkStrip): the ruler goes; the strip
  // closes its row itself.
  const clearInkRuler = useCallback(() => setInkRuler(null), []);
  // Leaving a page: the ruler goes (its row with it).
  useEffect(() => () => { hideInkRuler(); }, [focusedBlockId]); // eslint-disable-line react-hooks/exhaustive-deps
  // The ruler (More → Ruler, R): laid level across the surface in view
  // (pasteTarget's) with its row open, or taken away with its row.
  function toggleInkRuler() {
    if (inkRuler) { hideInkRuler(); return; }
    const target = pasteTarget();
    if (!target || readOnly) return;
    inkRulerSizeRef.current = target.size;
    setInkRuler(defaultRuler(target.page, visiblePart(target.page, target.size) || { x: 0, y: 0, ...target.size }));
    openRulerRow();
  }
  function hideInkRuler() {
    setInkRuler(null);
    closeRulerRow();
  }
  // The ruler's row: Snap 15° (the ruler lands on the grid at once) or
  // Free, the ticks' units, Hide.
  function inkRulerOptions(patch) {
    if (patch.hide) { hideInkRuler(); return; }
    if ("units" in patch) setInkRulerUnits(patch.units);
    if ("snap" in patch) setInkRuler((r) => (r ? { ...r, snap: !!patch.snap, angle: patch.snap ? snapAngle(r.angle) : r.angle } : r));
  }
  // Stamp line: a line along the ruler's middle, end to end, in the armed
  // preset's look (else the stylus pen's), one stroke on the ruler's
  // surface (ruler.js stampLine), with Undo on the pill.
  async function stampRulerLine() {
    const ruler = inkRuler;
    if (!ruler || readOnly || inkStale) return;
    const armed = inkVisible.find((x) => x.id === inkUi.tool);
    const size = inkRulerSizeRef.current || pasteTarget()?.size;
    if (!size) return;
    await handleInkStroke(ruler.surface, stampLine(ruler, armed ? toolStyle(armed) : inkPen, { t0: Date.now() }), size,
      { label: T("ink straight line") });
    undoableInkStatus(t("Line stamped along the ruler."));
  }
  // The layers' onRuler: the ruler moved, turned or stretched on its
  // surface (null: put away).
  const onRuler = (next) => { if (next) setInkRuler(next); else hideInkRuler(); };
  // The ruler goes, its row with it, once the viewer no longer shows its
  // surface: a PDF page's with the PDF hidden or the notebook view in its
  // place, a sheet's when the notebook view closes (leaving the page takes
  // it too, above).
  const inkRulerShown = !inkRuler || (typeof inkRuler.surface === "number" ? !pdfHidden && !notebook : notebook);
  useEffect(() => { if (!inkRulerShown) hideInkRuler(); }, [inkRulerShown]); // eslint-disable-line react-hooks/exhaustive-deps
  return { inkRuler, toggleInkRuler, hideInkRuler, clearInkRuler, inkRulerOptions, stampRulerLine, onRuler };
}
