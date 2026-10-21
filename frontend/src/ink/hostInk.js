// The iPad shell's side of a stroke (docs/dev/ipad.md "The pen"): what the
// claim tells the shell of the preset's look, the host's samples as the
// pointer events the layer samples, and a shape between page points and the
// host's viewport css px. Pure; InkLayer's claim (claimForHost) uses all of
// it, and the committed stroke stays the web's: the look only lets the
// shell's live line come close to it.
import { DEFAULT_SIZE, KNOWN_BRUSHES, dashPattern, mapShape, strokeNib } from "./ink.js";

const RAD = Math.PI / 180;

// The claim's `tool` for a stroke drawn in `look` (the layer's look: the
// preset's style with `pen` false when the stroke reads no pressure) at
// `k` css px per pt: {kind, color, size: the width on screen in css px,
// opacity}, then the look, each key left out at its default so an older
// shell, which reads only the four, draws the plain line it always drew —
// `brush` for a pen of a known brush other than the plain Pen; `pressure:
// false` when the stroke reads no pressure (Pressure-sensitive strokes
// off); `dash`, the preset's [on, off] (ink.js DASH × size) in css px;
// `blend: 'multiply'` for a highlighter, which multiplies onto the page;
// `stabilize`, the preset's smoothing level 1–4; for a calligraphy pen
// `nib`, its angle 0..179 (the preset's, as toolStyle gives it), and
// `nibRoll: true` unless the preset locks the angle, so the shell turns the
// nib by the Pencil's roll at the stroke's first sample as the page does.
// The ruler's `snap` is the layer's to add, beside the look.
export function claimTool(look, { k = 1 } = {}) {
  const kind = look.tool === "highlighter" ? "highlighter" : "pen";
  const tool = { kind, color: look.color, size: (look.size ?? DEFAULT_SIZE) * k, opacity: look.opacity ?? 1 };
  if (kind === "pen" && KNOWN_BRUSHES.has(look.brush)) tool.brush = look.brush;
  if (look.pen === false) tool.pressure = false;
  const dash = dashPattern(look);
  if (dash) tool.dash = dash.map((v) => v * k);
  if (kind === "highlighter") tool.blend = "multiply";
  const level = Math.round(Number(look.stabilize));
  if (level >= 1 && level <= 4) tool.stabilize = level;
  if (tool.brush === "calligraphy") {
    tool.nib = strokeNib(look.nib);
    if (!look.nibLock) tool.nibRoll = true;
  }
  return tool;
}

// A host sample, [x, y, p, t], [x, y, p, t, a, z] or, from a shell with
// `pencilRoll`, [x, y, p, t, a, z, r] (viewport css px, the pressure 0..1,
// ms from the first sample, the altitude and azimuth in degrees, the barrel
// roll in degrees clockwise as seen), as the pointer event inkSample
// reads: the angles turned to radians, as a browser reports them, so
// penTilt rounds them and drops an upright pencil as it does a browser's,
// and the roll as `twist`, which penTwist reads. Four values carry no tilt.
export function hostEvent(sample) {
  const [x, y, p, t, a, z, r] = sample;
  const ev = { clientX: x, clientY: y, pressure: p, timeStamp: t };
  if (Number.isFinite(a) && Number.isFinite(z)) {
    ev.altitudeAngle = a * RAD;
    ev.azimuthAngle = z * RAD;
  }
  if (Number.isFinite(r)) ev.twist = r;
  return ev;
}

// A shape record {kind, params} between page points and the host's
// viewport css px, for the layer whose page corner is at (left, top) at k
// css px per pt (the rect and zoom of the stroke's down): its points moved,
// its lengths scaled, its angle kept, every number on the 1/100 grid
// (ink.js mapShape). Undefined for a kind mapShape does not know.
export const shapeToHost = (shape, { left, top, k }) =>
  mapShape(shape, { point: (x, y) => [left + x * k, top + y * k], scale: k });
export const shapeFromHost = (shape, { left, top, k }) =>
  mapShape(shape, { point: (x, y) => [(x - left) / k, (y - top) / k], scale: 1 / k });
