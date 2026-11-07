// Copy as SVG and Download SVG (docs/dev/handwriting_ai.md "SVG"): strokes
// as a standalone SVG document, drawn from the very paths the layer draws
// (ink.js strokePath) with the attributes InkLayer.jsx `Strokes` gives
// them, so the picture is the handwriting as the page shows it. Pure, so it
// runs under node --test (frontend/tests/inkSvg.test.mjs).
//
// What differs from the screen, on purpose: colours are the stored ones,
// never a dark page's inversion (a CSS filter of the layer, app.css); a
// pencil's grain is its plain colour (the grain tile is the layer's
// <pattern>, a data URI per colour, which a file pasted elsewhere need not
// carry); and a highlighter multiplies through its own style, as the layer
// draws one inside a plane of its own. The ground is transparent, except
// with a `background` (a sheet of dark paper's colour, useInkSelection.js):
// ink on dark paper is written light and would vanish on the white it is
// pasted on, so the document carries the paper, and a highlighter is drawn
// without its blend, as the sheet draws one (notebook.css), since one
// multiplied into dark paper all but vanishes.
import { INK_COLORS } from "../shared/model/palette.js";
import { FILL_OPACITY, round3, strokeBounds, strokePath, strokePlane, unionBox } from "./ink.js";

const SVG_NS = "http://www.w3.org/2000/svg";

// An attribute value as XML takes it.
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
// A number as an attribute writes it: three decimals at most, no -0.
const num = (v) => String(round3(v) || 0);
// The 1/100 grid, outward: the document's corner down, its size up (the
// epsilon keeps 12.3 × 100 = 1230.0000000000002 at 12.3).
const floor2 = (v) => Math.floor(v * 100 + 1e-6) / 100;
const ceil2 = (v) => Math.ceil(v * 100 - 1e-6) / 100;
// An opacity attribute, left out at 1 (and above).
const alpha = (v) => (v < 1 ? num(Math.max(0, v)) : null);

// One <path>, its attributes in the order given, null ones left out.
function path(attrs) {
  let out = "<path";
  for (const [k, v] of Object.entries(attrs)) if (v != null) out += ` ${k}="${esc(v)}"`;
  return out + "/>";
}

// A stroke's paths, in the order Strokes draws them: a filled closed
// shape's inside first, under its line; then a pencil's shade bands, or the
// stroked centreline (a dashed constant-width pen, a highlighter, an unknown
// tool), or the filled outline every other pen is. `blend`: a highlighter
// multiplies.
function strokePaths(s, blend = true) {
  const p = strokePath(s);
  const paint = s.color || INK_COLORS[0], opacity = s.opacity ?? 1;
  const out = [];
  if (p.fill && s.fill?.color) out.push(path({ d: p.fill, fill: s.fill.color, "fill-opacity": alpha(s.fill.opacity ?? FILL_OPACITY) }));
  if (p.bands) {
    for (const b of p.bands) if (b.d) out.push(path({ d: b.d, fill: paint, "fill-opacity": alpha(b.alpha * opacity) }));
  } else if (p.d && p.stroke) {
    out.push(path({
      d: p.d, fill: "none", stroke: paint, "stroke-width": num(p.width), "stroke-opacity": alpha(opacity),
      "stroke-linecap": "round", "stroke-linejoin": "round", "stroke-dasharray": p.dash ?? null,
      style: p.multiply && blend ? "mix-blend-mode:multiply" : null,
    }));
  } else if (p.d) {
    out.push(path({ d: p.d, fill: paint, "fill-opacity": alpha(opacity) }));
  }
  return out;
}

// The pt of room around the strokes on every side.
const PAD = 4;

// `strokes` (any groups', in the order given) → {markup, width, height}: an
// SVG document `width` × `height` pt around their union box (strokeBounds,
// the drawn width included) grown by PAD on every side, its corner and
// size on the 1/100 grid (outward), the strokes in the layer's draw order,
// plane by plane (ink.js strokePlane: band 0's other strokes, band 0's
// highlighters, band 1's other strokes, band 1's highlighters), each plane
// in the given order. With `background` (a colour), a rect of it under them
// all, and highlighters unblended (see above); without, nothing under them.
// null when no stroke has a sample.
export function inkSvg(strokes, { background = null } = {}) {
  const drawn = (strokes || []).filter((s) => strokeBounds(s));
  const box = drawn.reduce((acc, s) => unionBox(acc, strokeBounds(s)), null);
  if (!box) return null;
  const x0 = floor2(box[0] - PAD), y0 = floor2(box[1] - PAD);
  const width = ceil2(box[2] + PAD - x0), height = ceil2(box[3] + PAD - y0);
  // (a stable sort: each plane keeps the order given)
  const paths = [...drawn].sort((a, b) => strokePlane(a) - strokePlane(b)).flatMap((s) => strokePaths(s, !background));
  const markup = [
    `<svg xmlns="${SVG_NS}" width="${num(width)}" height="${num(height)}" viewBox="0 0 ${num(width)} ${num(height)}">`,
    ...(background ? [`<rect width="${num(width)}" height="${num(height)}" fill="${esc(background)}"/>`] : []),
    `<g transform="translate(${num(-x0)} ${num(-y0)})">`,
    ...paths,
    "</g>",
    "</svg>",
    "",
  ].join("\n");
  return { markup, width, height };
}
