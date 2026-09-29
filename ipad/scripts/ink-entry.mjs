import * as ink from '../../frontend/src/ink/ink.js';
import { getStroke } from '../../frontend/node_modules/perfect-freehand/dist/esm/index.mjs';

// JavaScriptCore runs the same pure codec and editing functions as the browser.
// Only platform drawing is different: UIKit consumes the outline as CGPath.
const round = value => Math.round(value * 100) / 100;
export function geometry(stroke) {
  const samples = ink.decodeStroke(stroke);
  const line = stroke.tool === 'highlighter';
  const points = line ? samples.map(p => [p.x, p.y]) : getStroke(
    samples.map(p => [p.x, p.y, p.p ?? 0.5]), ink.outlineOptions(stroke));
  return { line, points: points.map(p => p.map(round)),
    midpoints: points.map((p, i) => p.map((v, axis) => round((v + points[(i + 1) % points.length][axis]) / 2))), size: stroke.size };
}
globalThis.GammaInk = { ...ink, geometry };
