// Text in figures (src/pdf/pageFigures.js): the boxes an operator list paints,
// the regions they make, and the blocks those regions leave original. The
// danger cases are the ones measured on the library, as synthetic pages, and
// the last tests run a small PDF through the real pdf.js.
import test from "node:test";
import assert from "node:assert/strict";
import { OPS, getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";
import { figureRegions, markFigures, pageRegions, paintedBoxes } from "../src/pdf/pageFigures.js";
import { pageFlow } from "../src/pdf/pageFlow.js";
import { pageFaces } from "../src/pdf/pdfFonts.js";
import { segmentPage, textRuns } from "../src/pdf/pdfTranslate.js";
import { createPlanner } from "../src/pdf/translatePlan.js";

const W = 612, H = 792;
const UPRIGHT = [1, 0, 0, -1, 0, H];

// An operator list, written call by call.
function ops() {
  const list = { fnArray: [], argsArray: [] };
  const op = (fn, ...args) => { list.fnArray.push(fn); list.argsArray.push(args.length ? args : null); return api; };
  const path = (x, y, w, h) => op(OPS.constructPath, [OPS.rectangle], [x, y, w, h], null);
  const api = { list, op, path, rect: (x, y, w, h, paint = OPS.fill) => path(x, y, w, h).op(paint) };
  return api;
}

const box = (x1, y1, x2, y2, k = "fill") => ({ k, b: [x1, y1, x2, y2] });

// A plot's marks in a box: its frame and ticks along two sides of it.
function plot(x1, y1, x2, y2) {
  const out = [box(x1, y1, x2, y2, "stroke")];
  for (let k = 1; k <= 6; k++) {
    out.push(box(x1 + ((x2 - x1) * k) / 7, y2 - 3, x1 + ((x2 - x1) * k) / 7 + 0.5, y2, "stroke"));
    out.push(box(x1, y1 + ((y2 - y1) * k) / 7, x1 + 3, y1 + ((y2 - y1) * k) / 7 + 0.5, "stroke"));
  }
  return out;
}

const PROSE = "the ensemble of atoms shares one excitation and loses its coherence when the blockade is broken by the probe";
// A block as the segmenter makes it, one line every 12 points.
const block = (text, x1, y1, x2, { nLines = 1, size = 10, translate = true } = {}) => ({
  text, src: text, spans: [], size, nLines, indent: nLines > 1 ? 0 : null, translate, x1, x2, y1, y2: y1 + nLines * 12,
  lines: Array.from({ length: nLines }, (_, k) => ({ x1, x2, y1: y1 + k * 12, y2: y1 + (k + 1) * 12 })),
});
// The page's body: a long paragraph clear of whatever the test draws.
const body = () => block(`Body text of the page, ${PROSE}.`, 54, 620, 558, { nLines: 12 });

const marked = (paras, boxes) => {
  markFigures(paras, pageRegions(boxes, W, H));
  return paras.filter((p) => p.figure).map((p) => p.text);
};

// --- what an operator list paints --------------------------------------------

test("boxes follow the CTM through save, transform and restore, into the viewport's frame", () => {
  const { list, op, rect } = ops();
  op(OPS.save).op(OPS.transform, 2, 0, 0, 2, 100, 100);
  rect(0, 0, 10, 20);
  op(OPS.restore);
  rect(0, 0, 10, 20, OPS.stroke);
  assert.deepEqual(paintedBoxes(list, UPRIGHT, OPS), [
    { k: "fill", b: [100, 652, 120, 692] }, { k: "stroke", b: [0, 772, 10, 792] },
  ]);
});

test("boxes come out in the blocks' frame under /Rotate and a crop box", () => {
  const { list, rect } = ops();
  rect(100, 200, 50, 10);
  // pdf.js's scale-1 viewports: /Rotate 90 and 270 on a 612 × 792 page, and a
  // crop box from (50, 40), 500 high.
  assert.deepEqual(paintedBoxes(list, [0, 1, 1, 0, 0, 0], OPS)[0].b, [200, 100, 210, 150]);
  assert.deepEqual(paintedBoxes(list, [0, -1, -1, 0, H, W], OPS)[0].b, [582, 462, 592, 512]);
  assert.deepEqual(paintedBoxes(list, [1, 0, 0, -1, -50, 540], OPS)[0].b, [50, 330, 100, 340]);
});

test("a clip cuts what is painted under it until it is restored", () => {
  const { list, op, path, rect } = ops();
  op(OPS.save);
  path(0, 0, 100, 100).op(OPS.clip).op(OPS.endPath);
  rect(50, 50, 100, 100);
  rect(200, 200, 10, 10); // outside: nothing shows
  op(OPS.restore);
  rect(50, 50, 100, 100);
  assert.deepEqual(paintedBoxes(list, [1, 0, 0, 1, 0, 0], OPS).map((b) => b.b), [[50, 50, 100, 100], [50, 50, 150, 150]]);
});

test("a form XObject brings its matrix and its bounding box, and gives them back", () => {
  const { list, op, rect } = ops();
  op(OPS.paintFormXObjectBegin, [1, 0, 0, 1, 300, 300], [0, 0, 20, 20]);
  rect(0, 0, 40, 10);
  op(OPS.paintFormXObjectEnd);
  rect(0, 0, 40, 10);
  assert.deepEqual(paintedBoxes(list, [1, 0, 0, 1, 0, 0], OPS).map((b) => b.b), [[300, 300, 320, 310], [0, 0, 40, 10]]);
});

test("images are the unit square under the CTM, repeated ones each at its place, and a shading fills its clip", () => {
  const { list, op, path } = ops();
  op(OPS.save).op(OPS.transform, 30, 0, 0, 20, 10, 10).op(OPS.paintImageXObject, "img", 1, 1).op(OPS.restore);
  op(OPS.paintImageXObjectRepeat, "img", 5, 5, [100, 100, 200, 100]);
  path(0, 0, 8, 8).op(OPS.clip).op(OPS.endPath).op(OPS.shadingFill, "sh");
  assert.deepEqual(paintedBoxes(list, [1, 0, 0, 1, 0, 0], OPS), [
    { k: "image", b: [10, 10, 40, 30] }, { k: "image", b: [100, 100, 105, 105] }, { k: "image", b: [200, 100, 205, 105] },
    { k: "shade", b: [0, 0, 8, 8] },
  ]);
});

test("a curve's box holds its control points, and a box drawn twice counts once", () => {
  const { list, op } = ops();
  op(OPS.constructPath, [OPS.moveTo, OPS.curveTo], [0, 0, 10, 40, 20, 40, 30, 0], null).op(OPS.stroke);
  op(OPS.constructPath, [OPS.moveTo, OPS.curveTo], [0, 0, 10, 40, 20, 40, 30, 0], null).op(OPS.stroke);
  assert.deepEqual(paintedBoxes(list, [1, 0, 0, 1, 0, 0], OPS), [{ k: "stroke", b: [0, 0, 30, 40] }]);
});

test("annotations are no drawing of the page", () => {
  const { list, op, rect } = ops();
  rect(0, 0, 10, 10);
  op(OPS.beginAnnotation, "a1", [0, 0, 100, 100], [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, 0], false);
  rect(20, 20, 10, 10);
  op(OPS.endAnnotation);
  assert.equal(paintedBoxes(list, [1, 0, 0, 1, 0, 0], OPS).length, 1);
});

// --- the rule ----------------------------------------------------------------

test("a plot's tick labels and axis titles stay original; what is written around it does not", () => {
  const paras = [
    body(),
    block("Time (ms)", 200, 402, 260),
    block("Probability density", 84, 250, 98),
    block("Atom loss", 220, 215, 280),
    block("Fig. 1. The measured signal against the probe's detuning.", 100, 420, 500),
    block("Paragraph beside the plot, " + PROSE, 400, 200, 558, { nLines: 12 }),
  ];
  assert.deepEqual(marked(paras, plot(100, 200, 380, 400)), ["Time (ms)", "Probability density", "Atom loss"]);
  for (const p of paras.filter((b) => b.figure)) assert.equal(p.translate, false);
  assert.ok(paras.filter((b) => !b.figure).every((b) => b.translate && !b.overFigure), "the rest is untouched");
});

test("a heading larger than the body and prose of eight words stay translated over a figure, marked as over it", () => {
  const paras = [
    body(),
    block("Atom Loss Rates", 150, 250, 300, { size: 14 }),
    block("each curve here shows the mean over twenty runs", 120, 300, 360),
    block("Rate", 150, 350, 200),
  ];
  assert.deepEqual(marked(paras, plot(100, 200, 380, 400)), ["Rate"]);
  assert.ok(paras[1].translate && paras[1].overFigure);
  assert.ok(paras[2].translate && paras[2].overFigure);
  assert.ok(!paras[0].overFigure);
});

test("a picture of a few images is a region, a small one is not", () => {
  const label = () => [body(), block("Panel label", 120, 300, 200)];
  assert.deepEqual(marked(label(), [box(100, 200, 380, 400, "image")]), ["Panel label"]);
  assert.deepEqual(marked(label(), [box(100, 296, 120, 316, "image")]), []);
});

test("a gradient bar is a picture, a strip of one included: its labels stay original", () => {
  // figs p2's panel (d): a shading under two labels, an axis and a marker, 24 pt tall.
  const panel = [box(340, 344, 551, 361, "shade"), box(335, 361, 554, 362, "stroke"), box(422, 343, 423, 361, "stroke"),
    box(481, 356, 492, 367), box(552, 358, 560, 365)];
  const labels = () => [body(), block("Time-optimal", 346, 348, 383), block("Detuning-robust", 497, 348, 543)];
  assert.deepEqual(marked(labels(), panel), ["Time-optimal", "Detuning-robust"]);
  assert.deepEqual(marked(labels(), panel.slice(1)), [], "without the shading it is a few strokes");
  const thin = [box(340, 350, 551, 362, "shade")];
  assert.deepEqual(marked(labels(), thin), [], "a bar under 20 pt is a rule's worth");
});

test("a plot of a few strokes is a figure when tick numbers line its bottom and a side", () => {
  // gates p3's (b): a frame, each axis's ticks as one path, two curves, three
  // dashed lines and a legend's two strokes; its labels sit on the top edge.
  const strokes = [box(361, 286, 554, 290, "stroke"), box(357, 208, 361, 283, "stroke"), box(361, 194, 559, 286, "stroke"),
    box(361, 233, 559, 283, "stroke"), box(361, 203, 559, 233, "stroke"), box(409, 196, 410, 286, "stroke")];
  const n = (text, x1, y1) => block(text, x1, y1, x1 + 6, { translate: false, size: 8 });
  const ticks = () => [n("1.5", 344, 203), n("1", 350, 228), n("0.5", 344, 253), n("0", 350, 278),
    n("0", 360, 291), n("1", 408, 291), n("2 t [ω−1]", 448, 291), n("3", 504, 291)];
  const labels = () => [block("gate, T", 373, 190, 397, { size: 7 }), block("hold, t1", 420, 190, 447, { size: 7 })];
  assert.equal(pageRegions(strokes, W, H).length, 1);
  assert.equal(pageRegions(strokes, W, H)[0].sparse, true, "fewer than a figure's boxes");
  const paras = [body(), ...labels(), ...ticks()];
  assert.deepEqual(marked(paras, strokes), ["gate, T", "hold, t1"]);
  assert.ok(paras.slice(3).every((p) => p.inFigure), "its numbers stand in it");
  assert.deepEqual(marked([body(), ...labels(), ...ticks().slice(4)], strokes), [], "numbers below it alone");
  assert.deepEqual(marked([body(), ...labels(), ...ticks().slice(0, 4)], strokes), [], "numbers beside it alone");
  // Numbers inside the strokes are a table's cells, not an axis's ticks.
  const cells = [n("1.5", 380, 210), n("1", 420, 210), n("0.5", 460, 210), n("0", 380, 250), n("2", 420, 250), n("3", 460, 250)];
  assert.deepEqual(marked([body(), ...labels(), ...cells], strokes), []);
});

test("danger: an image box behind a paragraph takes nothing, and no short block in it", () => {
  const paras = [
    block("A paragraph set over a tinted image, " + PROSE, 100, 210, 380, { nLines: 6 }),
    block("Short label", 120, 320, 220),
    body(),
  ];
  assert.deepEqual(marked(paras, [box(100, 200, 380, 400, "image")]), []);
  assert.ok(paras.every((p) => !p.overFigure), "no region is left to overlap");
});

test("danger: boxes drawn around prose are no figure", () => {
  const paras = [
    block("Theorem 1. Inside a frame of many small strokes, " + PROSE, 110, 210, 370, { nLines: 8 }),
    block("Proof", 110, 320, 200),
    body(),
  ];
  const frame = [];
  for (let k = 0; k < 8; k++) frame.push(box(100 + 40 * k, 200, 142 + 40 * k, 201), box(100 + 40 * k, 399, 142 + 40 * k, 400));
  frame.push(box(100, 200, 101, 400), box(379, 200, 380, 400));
  assert.deepEqual(marked(paras, frame), []);
});

test("danger: table rules around cells of words: the Table caption above keeps them", () => {
  const cells = () => [
    body(),
    block("TABLE I. Parameters of the experiment.", 100, 170, 380),
    block("Trap depth", 120, 225, 200),
    block("Rabi frequency", 120, 265, 220),
    block("Detuning sign", 120, 305, 220),
  ];
  const rules = [];
  for (let k = 0; k <= 6; k++) rules.push(box(100, 200 + 30 * k, 240, 201 + 30 * k), box(240, 200 + 30 * k, 380, 201 + 30 * k));
  rules.push(box(100, 200, 101, 380), box(240, 200, 241, 380), box(379, 200, 380, 380));
  assert.deepEqual(marked(cells(), rules), []);
  // A figure's caption stands below it, so the same drawing there is a figure.
  const under = cells();
  under[1] = block("Figure 2. Parameters of the experiment.", 100, 390, 380);
  assert.equal(marked(under, rules).length, 3);
});

test("danger: a page's background and a band across it make no region", () => {
  const paras = [body(), block("Slide title", 100, 40, 300), block("Item one here", 120, 300, 300)];
  // Filled over 60% of the page: gone, and it ties nothing together.
  assert.deepEqual(marked(paras, [box(0, 0, W, H), box(100, 290, 110, 300, "image")]), []);
  // A band across the page, with its ornaments on it.
  const band = [box(0, 20, W, 80)];
  for (let k = 0; k < 14; k++) band.push(box(50 + 30 * k, 30, 70 + 30 * k, 60));
  assert.deepEqual(marked(paras, band), []);
});

test("danger: a cluster the whole page wide is no figure", () => {
  const paras = [body(), block("Short note", 120, 300, 300)];
  const wide = [];
  for (let k = 0; k < 40; k++) wide.push(box(k * 15.3, 280, (k + 1) * 15.3 + 1, 320));
  assert.deepEqual(marked(paras, wide), []);
});

test("danger: a scan is one big image: its OCR labels translate as they do without the rule", () => {
  const paras = [body(), block("Fig label", 120, 300, 300)];
  assert.deepEqual(marked(paras, [box(10, 10, W - 10, H - 10, "image")]), []);
  assert.ok(paras.every((p) => p.translate));
});

test("a rule across the page ties nothing together", () => {
  const twoPlots = [...plot(100, 100, 300, 200), ...plot(100, 300, 300, 400), box(50, 250, 550, 251), box(200, 100, 201, 400)];
  assert.equal(pageRegions(twoPlots, W, H).length, 2);
});

test("panels near each other are one figure, unless that would take in running text", () => {
  // A legend over both panels, half in each of them.
  const legend = () => [body(), block("solid data dashed fit", 160, 300, 300)];
  const near = [...plot(100, 200, 210, 400), ...plot(230, 200, 340, 400)];
  assert.equal(pageRegions(near, W, H).length, 2);
  assert.deepEqual(marked(legend(), near), ["solid data dashed fit"]);
  // Panels at a corner: their union holds a paragraph, so they stay two, and
  // what stands in the union's other corner is no label.
  const corner = [...plot(100, 100, 250, 200), ...plot(270, 220, 420, 320)];
  const paras = [body(), block("Text in the corner, " + PROSE, 270, 100, 420, { nLines: 6 }), block("Far label", 110, 280, 200)];
  assert.deepEqual(marked(paras, corner), []);
});

test("CJK text counts its characters as words", () => {
  const paras = [
    body(),
    block("横轴表示时间", 120, 300, 200),
    block("这一段中文说明写在图的里面但它是连续的正文而不是标签所以仍然需要翻译", 120, 330, 370),
  ];
  assert.deepEqual(marked(paras, plot(100, 200, 380, 400)), ["横轴表示时间"]);
});

test("no region changes nothing, and blocks already original stay as they are", () => {
  const paras = [body(), block("Label", 120, 300, 200), block("0.0 0.5 1.0", 120, 320, 200, { translate: false })];
  assert.deepEqual(markFigures(paras, []), [], "no figures to keep text out of");
  assert.ok(paras.every((p) => !p.figure && !p.overFigure));
  assert.equal(markFigures([...paras].map((p) => ({ ...p })), pageRegions(plot(100, 200, 380, 400), W, H)).length, 1, "it gives the figures");
  assert.deepEqual(marked(paras, plot(100, 200, 380, 400)), ["Label"]);
  assert.equal(paras[2].figure, undefined);
  assert.equal(paras[2].inFigure, true, "it stands in the figure");
  assert.equal(paras[0].inFigure, undefined);
});

// --- the flow ----------------------------------------------------------------

const planOf = (paras) => createPlanner({ paras: () => paras, inJob: (pn) => pn === 1 }).planPage(1);

test("a paragraph flows past the text in a figure into the next column", () => {
  const paras = [
    block(`In this scheme ${PROSE} and the population of the atoms will be`, 54, 500, 300, { nLines: 8 }),
    block("Time, T", 400, 150, 460),
    block(`measured again after the second pulse, ${PROSE}.`, 316, 300, 562, { nLines: 8 }),
  ];
  // Both columns' top lines are text, so the plot's labels would be body flow.
  markFigures(paras, pageRegions(plot(330, 60, 560, 200), W, H));
  assert.equal(paras[1].figure, true);
  const units = planOf(paras);
  assert.equal(units.length, 1);
  assert.deepEqual(units[0].targets.map((t) => t.i), [0, 2]);
});

test("a paragraph goes on past a figure's numbers over a page break, and they keep their place in the columns", () => {
  const before = [block(`In this scheme ${PROSE} and the scattering induced by near-`, 54, 500, 300, { nLines: 8 })];
  const after = [
    block("mF= -3 -2 -1 0", 120, 205, 260, { translate: false }),
    block("0.2 0.4 0.6 0.8", 120, 180, 260, { translate: false }),
    block(`resonant light is presented, ${PROSE}.`, 54, 300, 300, { nLines: 8 }),
    block(`A new paragraph in the second column, ${PROSE}.`, 316, 300, 562, { nLines: 8 }),
  ];
  const pages = new Map([[1, before], [2, after]]);
  const plan = () => createPlanner({ paras: (pn) => pages.get(pn) || null }).planPage(1)[0].targets;
  assert.deepEqual(plan(), [{ pn: 1, i: 0 }], "a block with no text stops the walk");
  const columns = pageFlow(after).col;
  markFigures(after, pageRegions(plot(100, 60, 280, 200), W, H));
  assert.deepEqual(after.map((p) => !!p.inFigure), [true, true, false, false]);
  assert.deepEqual(plan(), [{ pn: 1, i: 0 }, { pn: 2, i: 2 }]);
  assert.deepEqual(pageFlow(after).col, columns);
  assert.deepEqual(pageFlow(after).flow, [2, 3]);
});

test("the text in a figure reads as no display equation after a one-line block", () => {
  const paras = [
    block(`In this scheme ${PROSE} and in the rotating frame the`, 54, 100, 300, { nLines: 8 }),
    block("Hamiltonian reads", 316, 200, 400),
    block("Time, T", 400, 250, 460),
    block(`A new paragraph, ${PROSE}.`, 316, 340, 562, { nLines: 8 }),
  ];
  markFigures(paras, pageRegions(plot(330, 235, 560, 300), W, H));
  assert.equal(paras[2].figure, true);
  assert.deepEqual(planOf(paras).map((u) => u.targets.map((t) => t.i)), [[0], [1], [3]]);
  // A block no text at all still is one: the planner's rule is unchanged.
  const equation = paras.map((p, i) => (i === 2 ? block("{v1} = {v2} + {v3}", 340, 250, 500, { translate: false }) : { ...p }));
  assert.deepEqual(planOf(equation).map((u) => u.targets.map((t) => t.i)), [[0, 1], [3]]);
});

// --- a page of pdf.js --------------------------------------------------------

test("a failing operator list gives no regions", async () => {
  const page = { getViewport: () => ({ transform: UPRIGHT, width: W, height: H }), getOperatorList: async () => { throw new Error("gone"); } };
  assert.deepEqual(await figureRegions(page), []);
  assert.deepEqual(await figureRegions({ getViewport: () => { throw new Error("no page"); } }), []);
  assert.deepEqual(await figureRegions({ ...page, getOperatorList: async () => ({ fnArray: [], argsArray: [] }) }), []);
});

// A one-page PDF: a paragraph of Helvetica under a plot with its labels, on a
// page of `media` and `extra` keys (/Rotate, /CropBox), the content under
// `matrix`.
function pdfWith({ media = "0 0 612 792", extra = "", matrix = "1 0 0 1 0 0" } = {}) {
  const text = (x, y, s, size = 10) => `BT /F1 ${size} Tf ${x} ${y} Td (${s}) Tj ET`;
  const lines = [
    "The ensemble of atoms shares one excitation and loses its",
    "coherence when the blockade is broken by the probe beam,",
    "so the signal in the plot falls with the probe's detuning",
    "and with the time the atoms spend in the excited state.",
    "A second run with the trap switched off shows the same",
    "decay, which rules out heating as the cause of the loss.",
  ];
  const marks = ["1 w", "150 300 300 200 re S"];
  for (let k = 1; k <= 8; k++) marks.push(`${150 + 33 * k} 300 m ${150 + 33 * k} 305 l S`, `150 ${300 + 22 * k} m 155 ${300 + 22 * k} l S`);
  const content = [`q ${matrix} cm`, ...marks, text(250, 288, "Time in ms"), text(300, 450, "Atom loss"),
                   ...lines.map((s, k) => text(100, 200 - 14 * k, s)), text(100, 660, "Introduction to the signal", 14), "Q"].join("\n");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    `<< /Type /Page /Parent 2 0 R /MediaBox [${media}] ${extra} /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
  ];
  let out = "%PDF-1.4\n";
  const at = objects.map((o, k) => { const start = out.length; out += `${k + 1} 0 obj\n${o}\nendobj\n`; return start; });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${at.map((n) => `${String(n).padStart(10, "0")} 00000 n \n`).join("")}`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return new TextEncoder().encode(out);
}

async function readPage(data) {
  const doc = await getDocument({ data, verbosity: 0 }).promise;
  const page = await doc.getPage(1);
  const vp = page.getViewport({ scale: 1 });
  const tc = await page.getTextContent();
  const faces = await pageFaces(page, new Set(tc.items.map((it) => it.fontName)), new Map());
  const paras = segmentPage(textRuns(tc.items, vp.transform, { faces, styles: tc.styles }));
  const regions = await figureRegions(page);
  markFigures(paras, regions);
  await doc.destroy();
  return { paras, regions, width: vp.width, height: vp.height };
}

const figureTexts = (paras) => paras.filter((p) => p.figure).map((p) => p.text.trim()).sort();

test("through pdf.js: the plot's labels stay original, its paragraph and heading do not", async () => {
  const { paras, regions } = await readPage(pdfWith());
  assert.equal(regions.length, 1);
  assert.deepEqual(figureTexts(paras), ["Atom loss", "Time in ms"]);
  const left = paras.filter((p) => p.translate).map((p) => p.text);
  assert.ok(left.some((t) => t.startsWith("The ensemble of atoms")), JSON.stringify(left));
  assert.ok(left.some((t) => t.startsWith("Introduction")), JSON.stringify(left));
});

test("through pdf.js: a crop box moves regions and blocks together", async () => {
  const plain = await readPage(pdfWith());
  const cropped = await readPage(pdfWith({ extra: "/CropBox [50 100 562 742]" }));
  assert.deepEqual([cropped.width, cropped.height], [512, 642]);
  assert.deepEqual(figureTexts(cropped.paras), figureTexts(plain.paras));
  const [p] = plain.regions, [c] = cropped.regions;
  assert.deepEqual([c.x1, c.y1, c.x2, c.y2], [p.x1 - 50, p.y1 - 50, p.x2 - 50, p.y2 - 50]);
});

// A page set on its side, its content turned back: it reads upright, so what
// it shows is the plain page, through the rotated viewport.
const TURNED = {
  90: { media: "0 0 792 612", extra: "/Rotate 90", matrix: "0 1 -1 0 792 0" },
  180: { media: "0 0 612 792", extra: "/Rotate 180", matrix: "-1 0 0 -1 612 792" },
  270: { media: "0 0 792 612", extra: "/Rotate 270", matrix: "0 -1 1 0 0 612" },
};
for (const [rotate, page] of Object.entries(TURNED)) {
  test(`through pdf.js: /Rotate ${rotate} gives the same regions and marks the same labels`, async () => {
    const plain = await readPage(pdfWith());
    const turned = await readPage(pdfWith(page));
    assert.deepEqual([turned.width, turned.height], [612, 792]);
    assert.deepEqual(turned.regions, plain.regions);
    assert.deepEqual(figureTexts(turned.paras), figureTexts(plain.paras));
  });
}
