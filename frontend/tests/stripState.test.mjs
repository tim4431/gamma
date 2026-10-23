// node --test tests/stripState.test.mjs (from frontend/) — the tool strip's
// state as functions of it (ink/stripState.js, useInkStrip.js's updates):
// what each update does to {open, tool, options, pen}, and the armed tool
// the layers draw with.
import assert from "node:assert/strict";
import { test } from "node:test";
import { toolStyle } from "../src/ink/ink.js";
import {
  armedTool, disarmedForPage, disarmedForReplay, hiddenDropped, optionsClosed, optionsToggled, penRestored, pencilUiApplied,
  picked, revealed, rulerRowClosed, rulerRowOpened, shapeStyleOf, stripClosed,
} from "../src/ink/stripState.js";

const tools = [
  { id: "pen1", kind: "pen", color: "#111111", size: 2 },
  { id: "hl1", kind: "highlighter", color: "#ffee00", size: 14 },
];
const state = (patch = {}) => ({ open: false, tool: null, options: false, pen: null, ...patch });

test("picking a pen makes it the stylus pen; a highlighter, a tool that is no preset and the hand do not", () => {
  assert.deepEqual(picked(state(), "pen1", tools), { open: true, tool: "pen1", options: false, pen: "pen1" });
  assert.deepEqual(picked(state({ pen: "pen1" }), "hl1", tools), { open: true, tool: "hl1", options: false, pen: "pen1" });
  for (const id of ["eraser", "select", "text", "shape", null]) {
    assert.equal(picked(state({ pen: "pen1" }), id, tools).pen, "pen1", String(id));
  }
  assert.equal(picked(state(), "pen9", tools, { kind: "pen" }).pen, "pen9", "a pen not in the list yet, by the kind its caller gives");
  assert.equal(picked(state(), "pen9", tools).pen, null, "an id the list lacks is no pen");
});

test("a pick closes the options row unless it opens it (only with a tool) or keeps it; the hand closes it", () => {
  const open = state({ open: true, tool: "pen1", options: true });
  assert.equal(picked(open, "hl1", tools).options, false);
  assert.equal(picked(open, "hl1", tools, { keepOptions: true }).options, true, "a duplicate stays editable");
  assert.equal(picked(state({ options: "ruler" }), "pen1", tools, { keepOptions: true }).options, "ruler", "the row kept is whichever was open");
  assert.equal(picked(state(), "shape", tools, { open: true }).options, true, "the Shapes tool from More or S");
  assert.equal(picked(open, null, tools, { open: true }).options, false, "the hand never opens a row");
  assert.equal(picked(open, null, tools, { keepOptions: true }).options, false, "nor keeps one");
  assert.equal(picked(state(), null, tools).open, true, "a pick shows the strip");
});

test("leaving a page disarms the tool and keeps the options row; a recording's Play closes the row too", () => {
  const armed = state({ open: true, tool: "pen1", options: true, pen: "pen1" });
  assert.deepEqual(disarmedForPage(armed), { ...armed, tool: null });
  assert.deepEqual(disarmedForReplay(armed), { ...armed, tool: null, options: false });
  const hand = state({ open: true, options: true });
  assert.equal(disarmedForPage(hand), hand, "nothing armed: the same state");
  assert.equal(disarmedForReplay(hand), hand);
});

test("restorePen gives the pen back only for a tool that is no preset, in an open strip, with a pen", () => {
  const after = state({ open: true, tool: "eraser", options: true, pen: "pen1" });
  assert.deepEqual(penRestored(after), { ...after, tool: "pen1", options: false });
  for (const tool of ["select", "text", "shape"]) assert.equal(penRestored({ ...after, tool }).tool, "pen1", tool);
  for (const s of [{ ...after, tool: "hl1" }, { ...after, tool: null }, { ...after, open: false }, { ...after, pen: null }]) {
    assert.equal(penRestored(s), s, JSON.stringify(s));
  }
});

test("the ruler's row and the tool's row toggle apart", () => {
  const s = state({ open: true, tool: "pen1" });
  assert.equal(optionsToggled(s).options, true, "no argument: the tool's");
  assert.equal(optionsToggled({ ...s, options: true }).options, false);
  assert.equal(optionsToggled({ ...s, options: "ruler" }).options, true, "the ruler's open is not the tool's: the tool's opens in its place");
  assert.equal(optionsToggled(s, "ruler").options, "ruler");
  assert.equal(optionsToggled({ ...s, options: "ruler" }, "ruler").options, false);
  assert.equal(optionsToggled({ ...s, options: true }, "ruler").options, "ruler", "the ruler's in the tool's place");
  assert.deepEqual(rulerRowOpened(s), { ...s, open: true, options: "ruler" });
  assert.deepEqual(rulerRowOpened(state()), { ...state(), open: true, options: "ruler" }, "laying the ruler shows the strip");
  assert.deepEqual(rulerRowClosed({ ...s, options: "ruler" }), { ...s, options: false });
  const toolRow = { ...s, options: true };
  assert.equal(rulerRowClosed(toolRow), toolRow, "putting the ruler away leaves the tool's row");
  assert.deepEqual(optionsClosed({ ...s, options: "ruler" }), { ...s, options: false });
});

test("the lasso shows the strip, its row closed; the strip closed disarms; a Pencil gesture patches; a hidden preset leaves the hand", () => {
  const s = state({ tool: "select", options: true, pen: "pen1" });
  assert.deepEqual(revealed(s), { ...s, open: true, options: false });
  const open = state({ open: true, tool: "pen1", options: "ruler", pen: "pen1" });
  assert.deepEqual(stripClosed(open), { open: false, tool: null, options: false, pen: "pen1" });
  assert.deepEqual(pencilUiApplied(state({ tool: "pen1" }), { open: true, options: true }), state({ open: true, tool: "pen1", options: true }));
  assert.deepEqual(pencilUiApplied(state(), { open: true, tool: "pen1", options: true }), state({ open: true, tool: "pen1", options: true }));
  assert.deepEqual(hiddenDropped(open), { ...open, tool: null, options: false });
});

test("armedTool is null for the hand, a read-only page, the build fence and the Text tool; else the tool's style", () => {
  const shapeStyle = { tool: "pen", color: "#111111", size: 2, opacity: 1, brush: "monoline" };
  const at = (patch) => armedTool({ tool: "pen1", readOnly: false, inkStale: false, inkTools: tools, inkShapeKind: "ellipse",
    inkShapeFill: null, inkShapeStyle: shapeStyle, ...patch });
  assert.equal(at({ tool: null }), null);
  assert.equal(at({ readOnly: true }), null);
  assert.equal(at({ inkStale: true }), null);
  assert.equal(at({ tool: "text" }), null, "the Text tool draws no ink");
  assert.equal(at({ tool: "gone" }), null, "a preset no longer in the list");
  assert.deepEqual(at({ tool: "eraser" }), { tool: "eraser" });
  assert.deepEqual(at({ tool: "select" }), { tool: "select" });
  assert.deepEqual(at({ tool: "shape", inkShapeFill: "#ffee00" }), { tool: "shape", kind: "ellipse", fill: "#ffee00", style: shapeStyle });
  assert.deepEqual(at({}), toolStyle(tools[0]));
  assert.deepEqual(at({ tool: "hl1" }), toolStyle(tools[1]));
});

test("the Shapes tool draws with the stylus pen's look as an even Ballpoint line, its smoothing, straightening and nib left behind", () => {
  const calligraphy = toolStyle({ id: "c", kind: "pen", color: "#222222", size: 3, brush: "calligraphy", nib: 30, nibLock: true, stabilize: 3, dash: "dashed" });
  assert.deepEqual(shapeStyleOf(calligraphy), { tool: "pen", color: "#222222", size: 3, opacity: 1, dash: "dashed", brush: "monoline" });
  const highlighter = toolStyle({ id: "h", kind: "highlighter", color: "#ffee00", size: 14, straight: "always" });
  const { straight: _s, ...look } = highlighter;
  assert.deepEqual(shapeStyleOf(highlighter), look, "a highlighter keeps its tool and gets no brush");
});
