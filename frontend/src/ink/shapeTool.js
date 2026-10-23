// The Shapes tool (docs/dev/handwriting.md "What the user sees" and
// "Client"), pure: the shape a drag from the down point to the pointer
// draws, as the shape record (shapes.js params) the stroke commits with —
// shapes.js shapeSamples turns it into the dense polyline that is drawn and
// saved. tests/shared/inkshapes.json `tool` pins every rule (its `about` is
// the normative statement); frontend/tests/shapeTool.test.mjs runs it.
import { round2 } from "./ink.js";
import { snapEnd } from "./shapes.js";

// What the tool draws: the values of the account's inkShapeKind.
export const SHAPE_TOOL_KINDS = ["line", "arrow", "rect", "ellipse"];
// A drag shorter than this on the screen (css px) draws nothing.
export const SHAPE_MIN_PX = 4;

// kind: one of SHAPE_TOOL_KINDS; from, to: {x, y} in page points (the down
// point and the pointer); shift: Shift is held; k: css px per pt. → {kind,
// params} | null. A line or an arrow runs from `from` to `to` (an arrow's
// tip at `to`, written without `head`: shapes.js draws min(length / 4, 24)),
// Shift turning it about `from` to the nearest 15°, its length kept. A
// rectangle is the box the two span, {cx, cy, w, h, angle: 0}; an ellipse
// the one inscribed in it, {cx, cy, rx, ry, angle} with rx >= ry (angle 0,
// or 90 for a box taller than wide). With Shift both take the square box
// whose side is the larger of |dx| and |dy|, laid from `from` toward `to`.
// Every number on the 1/100 pt grid.
export function toolShape(kind, from, to, { shift = false, k = 1 } = {}) {
  if (!from || !to) return null;
  let dx = to.x - from.x, dy = to.y - from.y;
  if (Math.hypot(dx, dy) * k < SHAPE_MIN_PX) return null;
  if (kind === "line" || kind === "arrow") {
    const end = shift ? snapEnd(from, to) : to;
    return { kind, params: { x1: round2(from.x), y1: round2(from.y), x2: round2(end.x), y2: round2(end.y) } };
  }
  if (kind !== "rect" && kind !== "ellipse") return null;
  if (shift) {
    const side = Math.max(Math.abs(dx), Math.abs(dy));
    dx = (Math.sign(dx) || 1) * side;
    dy = (Math.sign(dy) || 1) * side;
  }
  const cx = from.x + dx / 2, cy = from.y + dy / 2, w = Math.abs(dx), h = Math.abs(dy);
  if (kind === "rect") return { kind, params: { cx: round2(cx), cy: round2(cy), w: round2(w), h: round2(h), angle: 0 } };
  const wide = w >= h;
  return { kind, params: { cx: round2(cx), cy: round2(cy), rx: round2((wide ? w : h) / 2), ry: round2((wide ? h : w) / 2), angle: wide ? 0 : 90 } };
}
