// The tool strip's state as functions of it (useInkStrip.js `inkUi`;
// docs/dev/handwriting.md "Client"), pure: each update the strip makes,
// and the armed tool as the layers draw with it. frontend/tests/
// stripState.test.mjs pins them. The state is {open, tool, options, pen}:
// the strip shown; the armed tool (a preset id, "eraser", "select",
// "text", "shape", or null for the hand); the open options row (false,
// true for the armed tool's, "ruler" for the ruler's); and the pen preset
// a stylus writes with (the last pen armed).
import { toolStyle } from "./ink.js";
import { RESERVED_TOOLS } from "./pencilActions.js";

// A tool armed (pickInkTool), the strip open. A pen preset becomes the
// stylus pen: its `kind` as the caller gives it (a preset not in the list
// yet), else as `tools` has it. The options row closes unless the pick
// opens it (`open`: the Shapes tool from More or S) or keeps it
// (`keepOptions`: a duplicate stays editable); the hand has none.
export function picked(s, id, tools, { keepOptions = false, open = false, kind } = {}) {
  const k = kind || (id && tools.find((t) => t.id === id)?.kind);
  return { ...s, open: true, tool: id, options: open && !!id ? true : keepOptions && !!id ? s.options : false, pen: k === "pen" ? id : s.pen };
}
// A lasso selection: the strip shown, its options row closed.
export const revealed = (s) => ({ ...s, open: true, options: false });
// The ruler laid opens its row; put away, it takes its row with it.
export const rulerRowOpened = (s) => ({ ...s, open: true, options: "ruler" });
export const rulerRowClosed = (s) => (s.options === "ruler" ? { ...s, options: false } : s);
// The strip closed: the tools disarm.
export const stripClosed = (s) => ({ ...s, open: false, tool: null, options: false });
// An options row toggled: with no `which` the armed tool's (the ruler's
// open is not the tool's: the tool's opens in its place); "ruler", the
// ruler's.
export const optionsToggled = (s, which) => ({ ...s, options: which === "ruler" ? (s.options === "ruler" ? false : "ruler") : s.options !== true });
export const optionsClosed = (s) => ({ ...s, options: false });
// A Pencil gesture's patch of the strip (pencilActions.js {ui}).
export const pencilUiApplied = (s, ui) => ({ ...s, ...ui });
// A recording plays: the tool disarms and its row closes.
export const disarmedForReplay = (s) => (s.tool ? { ...s, tool: null, options: false } : s);
// Leaving a page: the tool disarms; the strip and its row stay.
export const disarmedForPage = (s) => (s.tool ? { ...s, tool: null } : s);
// The handwriting tour's end (the guide's restore("pen")): the pen last
// armed again, when the tour left a tool that is no preset armed in the
// open strip.
export const penRestored = (s) => (s.open && s.pen && RESERVED_TOOLS.includes(s.tool) ? { ...s, tool: s.pen, options: false } : s);
// A removed or hidden preset armed: the hand instead, its row closed.
export const hiddenDropped = (s) => ({ ...s, tool: null, options: false });

// What the Shapes tool draws with: the stylus pen's colour, width,
// opacity and line as an even Ballpoint line (a shape is constant width
// whatever the style); its smoothing, straightening and nib stay behind.
export function shapeStyleOf(inkPen) {
  const { stabilize: _s, straight: _st, nib: _n, nibLock: _l, brush: _b, ...look } = inkPen;
  return look.tool === "pen" ? { ...look, brush: "monoline" } : look;
}

// The armed tool as the layers draw with it (`inkTool`): null for the
// hand, on a read-only page and behind the build fence (`inkStale`);
// {tool} for the eraser and the lasso; the Shapes tool with the account's
// kind and fill and `inkShapeStyle`; else the preset's style (ink.js
// toolStyle), null for an id `inkTools` lacks (the Text tool's among them).
export function armedTool({ tool, readOnly, inkStale, inkTools, inkShapeKind, inkShapeFill, inkShapeStyle }) {
  if (!tool || readOnly || inkStale) return null;
  if (tool === "eraser" || tool === "select") return { tool };
  if (tool === "shape") return { tool: "shape", kind: inkShapeKind, fill: inkShapeFill, style: inkShapeStyle };
  const p = inkTools.find((x) => x.id === tool);
  return p ? toolStyle(p) : null;
}
