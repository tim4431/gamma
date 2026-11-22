// node --test tests/inkAi.test.mjs (from frontend/) — Redraw as shapes and
// Convert to text on the client (ink/inkAi.js, ink/inkAiClient.js): what a
// request sends and how its strokes are found again; the shape stroke a
// record becomes; the redraw's edits across groups, with the shapes whose
// strokes changed meanwhile left out; the text box a transcription takes
// and the strokes it removes, those moved or reshaped meanwhile left where
// they are now; a group deleted meanwhile read as gone; the server's caps
// on one request; and the request itself, against a stubbed server.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { MAX_BYTES, MAX_SAMPLES, MAX_STROKES, decodeStroke, encodeStroke, newCanvasInk, newInk, restyleStrokes, strokeBounds,
  transformStrokes, translateStrokes, unionBox } from "../src/ink/ink.js";
import { MAX_REDRAW_STROKES, inkAiRequest, inkInTree, penOnly, redrawPlan, requestTooBig, shapeStroke, textBoxFor, textPlan }
  from "../src/ink/inkAi.js";
import { selectedStrokes } from "../src/ink/selectionEdits.js";
import { shapeSamples } from "../src/ink/shapes.js";
import { LINE, PAD, normalizeTextBox } from "../src/markup/textBox.js";

// A freehand pen stroke from (x, y) along `len` pt to the right, timed.
const pen = (id, x = 10, y = 10, over = {}) => encodeStroke({ id, ch: "xypt", size: 2, t0: 5000,
  samples: [{ x, y, p: 0.5, t: 0 }, { x: x + 10, y: y + 4, p: 0.5, t: 40 }, { x: x + 20, y, p: 0.5, t: 90 }], ...over });
const highlighter = (id, x = 0, y = 0) => encodeStroke({ id, tool: "highlighter", ch: "xy", pen: false, size: 10, opacity: 0.6, color: "#ffe28f",
  samples: [{ x, y }, { x: x + 40, y }] });
const page = (...strokes) => ({ ...newInk(1, 612, 792), strokes });
const groupsOf = (groups) => (id) => groups[id] || null;
const ids = (ink) => ink.strokes.map((s) => s.id);
const counter = (prefix = "N") => { let n = 0; return () => `${prefix}${++n}`; };
const RECT = { cx: 30, cy: 20, w: 40, h: 20, angle: 0 };

test("penOnly: pens and strokes without a tool, never a highlighter", () => {
  const a = pen("a"), h = highlighter("h"), bare = { id: "b", ch: "xy", pts: [0, 0] };
  assert.deepEqual(penOnly([a, h, bare]), [a, bare]);
  assert.deepEqual(penOnly(null), []);
  assert.equal(MAX_REDRAW_STROKES, 300);
});

test("inkAiRequest: the selection's strokes in selectedStrokes' order, each with where it lives and its pts as sent", () => {
  const g1 = page(pen("a"), highlighter("h"), pen("b", 100)), g2 = { ...newInk(1, 600, 800), strokes: [pen("x", 300, 300)] };
  const inkOf = groupsOf({ g1, g2, g3: page(pen("q")) });
  const sel = { page: 1, items: [{ id: "gone", ids: ["z"] }, { id: "g3", ids: ["erased"] }, { id: "g1", ids: ["b", "h", "a"] }, { id: "g2", ids: ["x"] }] };
  const req = inkAiRequest(sel, inkOf);
  assert.deepEqual(req.strokes, selectedStrokes(sel, inkOf).strokes, "every tool, the strokes as the groups hold them");
  assert.equal(req.strokes[0], g1.strokes[0]);
  assert.deepEqual(req.refs, [
    { group: "g1", id: "a", pts: g1.strokes[0].pts }, { group: "g1", id: "h", pts: g1.strokes[1].pts },
    { group: "g1", id: "b", pts: g1.strokes[2].pts }, { group: "g2", id: "x", pts: g2.strokes[0].pts },
  ]);
  assert.equal(req.refs[0].pts, g1.strokes[0].pts, "the very array");
  assert.equal(req.page, 1);
  assert.equal(req.space, g1.space, "the first group's space");
  assert.deepEqual(req.box, [g1.strokes[0], g1.strokes[2], g2.strokes[0]].reduce((b, s) => unionBox(b, strokeBounds(s)), null),
    "the pen strokes' box, the highlighter's left out");
});

test("inkAiRequest: null with no pen stroke; an id a group before used goes out fresh, its ref keeps the real one", () => {
  const inkOf = groupsOf({ g1: page(highlighter("h"), pen("a")), g2: page(pen("a", 50)) });
  assert.equal(inkAiRequest({ page: 1, items: [{ id: "g1", ids: ["h"] }] }, inkOf), null);
  assert.equal(inkAiRequest(null, inkOf), null);
  assert.equal(inkAiRequest({ page: 1, items: [{ id: "g9", ids: ["a"] }] }, inkOf), null);
  const req = inkAiRequest({ page: 1, items: [{ id: "g1", ids: ["a"] }, { id: "g2", ids: ["a"] }] }, inkOf);
  assert.equal(req.strokes[0].id, "a");
  assert.notEqual(req.strokes[1].id, "a", "the server reads one file, where an id is unique");
  assert.deepEqual({ ...req.strokes[1], id: "a" }, inkOf("g2").strokes[0], "nothing else changes");
  assert.deepEqual(req.refs.map((r) => [r.group, r.id]), [["g1", "a"], ["g2", "a"]]);
  const canvas = inkAiRequest({ page: "sheet1", items: [{ id: "c", ids: ["a"] }] }, groupsOf({ c: { ...newCanvasInk(600, 800), strokes: [pen("a")] } }));
  assert.equal(canvas.page, "sheet1");
  assert.deepEqual(canvas.space, { kind: "canvas", width: 600, height: 800 });
});

// The samples a shape stroke holds, decoded, as shapeSamples gives them for its record.
const drawnAs = (s, span = 0) => assert.deepEqual(decodeStroke(s).map((q) => [q.x, q.y, q.t]),
  shapeSamples(s.shape.kind, s.shape.params, { span }).map((q) => [q.x, q.y, q.t]));

test("shapeStroke: a shape stroke as the Shapes tool commits one, for every kind the redraw writes", () => {
  const look = { color: "#1d4ed8", size: 1.4, opacity: 0.8 };
  const kinds = {
    line: { x1: 0, y1: 0, x2: 40, y2: 0 },
    arrow: { x1: 0, y1: 0, x2: 40, y2: 10, head: 8 },
    rect: RECT,
    ellipse: { cx: 50, cy: 50, rx: 30, ry: 15, angle: 20 },
    triangle: { x1: 0, y1: 0, x2: 40, y2: 0, x3: 20, y3: 30 },
    polygon: { cx: 0, cy: 0, r: 20, n: 6, angle: 0 },
    curve: { x1: 0, y1: 50, x2: 30, y2: 10, x3: 60, y3: 45, closed: false, smooth: true },
  };
  for (const [kind, params] of Object.entries(kinds)) {
    const s = shapeStroke(kind, params, look, { t0: 1234, span: 300 });
    assert.ok(s, kind);
    assert.equal(s.tool, "pen");
    assert.equal(s.brush, "monoline");
    assert.equal(s.pen, false);
    assert.equal(s.ch, "xyt");
    assert.deepEqual(s.shape, { kind, params });
    assert.notEqual(s.shape.params, params, "its own copy of the params");
    assert.equal(s.color, "#1d4ed8");
    assert.equal(s.size, 1.4);
    assert.equal(s.opacity, 0.8);
    assert.equal(s.t0, 1234);
    assert.equal(s.dash, undefined);
    assert.equal(s.fill, undefined);
    assert.match(s.id, /^[A-Za-z0-9]{8}$/);
    drawnAs(s, 300);
    assert.equal(decodeStroke(s).at(-1).t, kind === "line" || decodeStroke(s).length > 1 ? 300 : 0, "spread over the span");
  }
  assert.equal(shapeStroke("line", kinds.line, look).t0, undefined, "no t0 without one");
});

test("shapeStroke: a curve keeps its flags as booleans, open or closed, polyline or smooth", () => {
  const points = { x1: 0, y1: 0, x2: 40, y2: 0, x3: 40, y3: 30, x4: 0, y4: 30 };
  for (const flags of [{}, { closed: true }, { smooth: true }, { closed: true, smooth: true }]) {
    const s = shapeStroke("curve", { ...points, ...flags }, {});
    assert.deepEqual(s.shape.params, { ...points, ...flags });
    drawnAs(s);
    const pts = decodeStroke(s);
    assert.deepEqual([pts[0].x, pts[0].y], [0, 0]);
    assert.deepEqual([pts.at(-1).x, pts.at(-1).y], flags.closed ? [0, 0] : [0, 30], "a closed curve ends where it began");
  }
  assert.equal(shapeStroke("curve", { x1: 0, y1: 0, x2: 10, y2: 10, x4: 20, y4: 0 }, {}), null, "a gap in the numbering draws nothing");
  assert.equal(shapeStroke("curve", { x1: 0, y1: 0, closed: true }, {}), null, "one point");
});

test("shapeStroke: the look's line and fill (a fill on a closed kind alone), a circle as an ellipse, null for no shape", () => {
  const fill = { color: "#ff0000", opacity: 0.4 };
  const rect = shapeStroke("rect", RECT, { color: "#000000", size: 2, dash: "dotted", fill });
  assert.equal(rect.dash, "dotted");
  assert.deepEqual(rect.fill, fill);
  assert.equal(shapeStroke("line", { x1: 0, y1: 0, x2: 10, y2: 0 }, { fill }).fill, undefined, "a line has no inside");
  assert.equal(shapeStroke("curve", { x1: 0, y1: 0, x2: 10, y2: 0, x3: 5, y3: 9, closed: true }, { fill }).fill, undefined, "nor a curve");
  assert.equal(shapeStroke("rect", RECT, { dash: "wavy" }).dash, undefined, "a line that is no dash is solid");
  const defaults = shapeStroke("rect", RECT);
  assert.deepEqual([defaults.color, defaults.size, defaults.opacity], ["#1f1f1f", 1.6, 1]);
  const circle = shapeStroke("circle", { cx: 10, cy: 10, r: 5 }, {});
  assert.deepEqual(circle.shape, { kind: "ellipse", params: { cx: 10, cy: 10, rx: 5, ry: 5, angle: 0 } });
  drawnAs(circle);
  assert.equal(shapeStroke("star", { cx: 0, cy: 0 }, {}), null, "a kind that draws nothing");
  assert.equal(shapeStroke("rect", { ...RECT, w: NaN }, {}), null);
  assert.equal(shapeStroke("rect", { ...RECT, w: "40" }, {}), null);
  assert.equal(shapeStroke("rect", null, {}), null);
  assert.equal(shapeStroke("line", { x1: 0, y1: 0, x2: 10 }, {}), null, "a missing end");
  assert.equal(shapeStroke("rect", RECT, {}, { id: "mine" }).id, "mine");
});

test("redrawPlan: a shape over strokes of two groups lands in the first one's group, in its place; the rest leave theirs", () => {
  const g1 = page(pen("a"), pen("b", 40), pen("c", 80)), g2 = page(pen("x", 300), pen("y", 340));
  const inkOf = groupsOf({ g1, g2 });
  const req = inkAiRequest({ page: 1, items: [{ id: "g1", ids: ["b", "c"] }, { id: "g2", ids: ["x"] }] }, inkOf);
  const plan = redrawPlan(req, [{ kind: "rect", params: RECT, strokes: [2, 0] }], inkOf, counter());
  assert.equal(plan.count, 1);
  assert.equal(plan.skipped, 0);
  assert.deepEqual(plan.items, [{ id: "g1", ids: ["N1"] }]);
  assert.deepEqual(plan.changes.map((c) => [c.id, c.page, ids(c.after)]), [["g1", 1, ["a", "N1", "c"]], ["g2", 1, ["y"]]]);
  assert.equal(plan.changes[0].before, g1);
  assert.equal(plan.changes[1].before, g2);
  assert.equal(plan.changes[0].after.space, g1.space);
  assert.equal(plan.changes[0].after.strokes[0], g1.strokes[0], "the strokes it leaves are as they were");
  assert.deepEqual(plan.changes[0].after.strokes[1].shape, { kind: "rect", params: RECT });
  assert.deepEqual(ids(g1), ["a", "b", "c"], "the groups themselves are not touched");
});

test("redrawPlan: the new stroke takes the first replaced stroke's index; two shapes in one group, each in its place", () => {
  const g = page(pen("a"), pen("b"), pen("c"), pen("d"), pen("e"));
  const inkOf = groupsOf({ g });
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["b", "c", "d", "e"] }] }, inkOf);
  const plan = redrawPlan(req, [
    { kind: "line", params: { x1: 0, y1: 0, x2: 30, y2: 0 }, strokes: [3, 1] },
    { kind: "rect", params: RECT, strokes: [0] },
  ], inkOf, counter());
  assert.deepEqual(ids(plan.changes[0].after), ["a", "N2", "N1", "d"], "the line over e and c in c's place, the rect in b's");
  assert.deepEqual(plan.items, [{ id: "g", ids: ["N1", "N2"] }]);
  assert.equal(plan.count, 2);
});

test("redrawPlan: the look and timing of the first replaced stroke, its size held to a pen's", () => {
  const first = pen("a", 10, 10, { color: "#15803d", opacity: 0.7, dash: "dashed", size: 20, t0: 9000 });
  const inkOf = groupsOf({ g: page(first, pen("b", 40, 10, { color: "#ff0000", size: 1 })) });
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a", "b"] }] }, inkOf);
  const made = redrawPlan(req, [{ kind: "rect", params: RECT, strokes: [1, 0] }], inkOf, counter()).changes[0].after.strokes[0];
  assert.deepEqual([made.color, made.opacity, made.dash, made.size, made.t0], ["#15803d", 0.7, "dashed", 8, 9000]);
  assert.equal(decodeStroke(made).at(-1).t, 90, "over the stroke's own duration");
  const thin = groupsOf({ g: page(pen("a", 10, 10, { size: 0.2 })) });
  assert.equal(redrawPlan(inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a"] }] }, thin), [{ kind: "rect", params: RECT, strokes: [0] }], thin)
    .changes[0].after.strokes[0].size, 0.6);
  // a piece the eraser left starts later in its stroke: the shape starts there too
  const piece = encodeStroke({ id: "p", ch: "xyt", pen: false, t0: 1000, samples: [{ x: 0, y: 0, t: 200 }, { x: 30, y: 0, t: 260 }] });
  const pieceOf = groupsOf({ g: page(piece) });
  const timed = redrawPlan(inkAiRequest({ page: 1, items: [{ id: "g", ids: ["p"] }] }, pieceOf),
    [{ kind: "line", params: { x1: 0, y1: 0, x2: 30, y2: 0 }, strokes: [0] }], pieceOf).changes[0].after.strokes[0];
  assert.equal(timed.t0, 1200);
  assert.deepEqual(decodeStroke(timed).map((q) => q.t), [0, 60]);
  const untimed = encodeStroke({ id: "u", ch: "xy", pen: false, samples: [{ x: 0, y: 0 }, { x: 30, y: 0 }] });
  const untimedOf = groupsOf({ g: page(untimed) });
  const plain = redrawPlan(inkAiRequest({ page: 1, items: [{ id: "g", ids: ["u"] }] }, untimedOf),
    [{ kind: "line", params: { x1: 0, y1: 0, x2: 30, y2: 0 }, strokes: [0] }], untimedOf).changes[0].after.strokes[0];
  assert.equal(plain.t0, undefined);
  assert.deepEqual(decodeStroke(plain).map((q) => q.t), [0, 0]);
});

test("redrawPlan: a fill stays when the first stroke had one and the new kind has an inside", () => {
  const filled = shapeStroke("rect", RECT, { color: "#000000", size: 2, fill: { color: "#ff0000" } }, { id: "f" });
  const inkOf = groupsOf({ g: page(filled, { ...filled, id: "f2" }) });
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["f", "f2"] }] }, inkOf);
  const plan = redrawPlan(req, [
    { kind: "ellipse", params: { cx: 30, cy: 20, rx: 20, ry: 10, angle: 0 }, strokes: [0] },
    { kind: "line", params: { x1: 0, y1: 0, x2: 30, y2: 0 }, strokes: [1] },
  ], inkOf, counter());
  const [ellipse, line] = plan.changes[0].after.strokes;
  assert.deepEqual(ellipse.fill, { color: "#ff0000" });
  assert.equal(line.fill, undefined);
});

test("redrawPlan: a shape whose stroke changed or vanished meanwhile is skipped and counted; a restyle or the same numbers are no change", () => {
  const g = page(pen("a"), pen("b", 40), pen("c", 80), pen("d", 120));
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a", "b", "c", "d"] }] }, groupsOf({ g }));
  // meanwhile: a erased, b moved, c recoloured, d written again with the same numbers
  const moved = { ...g.strokes[1], pts: g.strokes[1].pts.map((v, i) => (i === 0 ? v + 100 : v)) };
  const now = { ...g, strokes: [moved, ...restyleStrokes({ ...g, strokes: [g.strokes[2]] }, ["c"], { color: "#0f766e" }).strokes,
    { ...g.strokes[3], pts: [...g.strokes[3].pts] }] };
  const inkOf = groupsOf({ g: now });
  const plan = redrawPlan(req, [
    { kind: "rect", params: RECT, strokes: [0, 2] },   // a is gone
    { kind: "rect", params: RECT, strokes: [1] },      // b moved
    { kind: "rect", params: RECT, strokes: [2] },      // c is as it was, in a new colour
    { kind: "rect", params: RECT, strokes: [3] },      // d's samples are the same numbers
  ], inkOf, counter());
  assert.equal(plan.skipped, 2);
  assert.equal(plan.count, 2);
  assert.deepEqual(ids(plan.changes[0].after), ["b", "N1", "N2"]);
  assert.equal(plan.changes[0].after.strokes[1].color, "#0f766e", "the look as the stroke is now");
  assert.equal(plan.changes[0].before, now);
  const gone = redrawPlan(req, [{ kind: "rect", params: RECT, strokes: [0] }], () => null);
  assert.deepEqual(gone, { changes: [], items: [], count: 0, skipped: 1 }, "a group that is gone");
});

test("redrawPlan: fresh ids its group never held, even from a makeStrokeId that repeats or names a stroke there", () => {
  const g = page(pen("a"), pen("b"), pen("c"));
  const inkOf = groupsOf({ g });
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a", "b", "c"] }] }, inkOf);
  const plan = redrawPlan(req, [0, 1, 2].map((i) => ({ kind: "rect", params: RECT, strokes: [i] })), inkOf, () => "c");
  const made = plan.items[0].ids;
  assert.equal(made.length, 3);
  assert.equal(new Set(made).size, 3, "unique");
  assert.ok(made.every((id) => !["a", "b", "c"].includes(id)), "none the group held, not even one it removes");
  assert.deepEqual(ids(plan.changes[0].after), made);
});

test("redrawPlan: indices that name no pen stroke, or a stroke an earlier shape took, are dropped; a shape that draws nothing is left out", () => {
  const g = page(highlighter("h"), pen("a"), pen("b", 40));
  const inkOf = groupsOf({ g });
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["h", "a", "b"] }] }, inkOf);
  const plan = redrawPlan(req, [
    { kind: "rect", params: RECT, strokes: [0, 1, 1, 7, -1, 1.5, "2"] },   // the highlighter, a repeat, out of range, no ints
    { kind: "line", params: { x1: 0, y1: 0, x2: 30, y2: 0 }, strokes: [1, 0] },   // a is taken, h never offered
    { kind: "star", params: { cx: 0, cy: 0 }, strokes: [2] },   // draws nothing
    { kind: "rect", params: RECT },
    null,
  ], inkOf, counter());
  assert.equal(plan.count, 1);
  assert.equal(plan.skipped, 0);
  assert.deepEqual(ids(plan.changes[0].after), ["h", "N1", "b"], "the highlighter stays");
  assert.deepEqual(redrawPlan(req, [], inkOf), { changes: [], items: [], count: 0, skipped: 0 });
});

test("redrawPlan: refused when a group would hold more than MAX_STROKES", () => {
  const strokes = Array.from({ length: MAX_STROKES + 1 }, (_, i) => ({ id: `s${i}`, ch: "xy", pen: false, pts: [i * 100, 0, 100, 0] }));
  const inkOf = groupsOf({ g: page(...strokes) });
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["s0"] }] }, inkOf);
  assert.deepEqual(redrawPlan(req, [{ kind: "rect", params: RECT, strokes: [0] }], inkOf), { refused: true });
  const fits = groupsOf({ g: page(...strokes.slice(0, MAX_STROKES)) });
  assert.equal(redrawPlan(req, [{ kind: "rect", params: RECT, strokes: [0] }], fits).count, 1, "at the cap is fine");
});

// Pen strokes in one colour, each `len` pt long, one per row of handwriting.
const row = (id, y, len, color = "#1f1f1f") => encodeStroke({ id, ch: "xy", pen: false, size: 2, color,
  samples: [{ x: 100, y }, { x: 100 + len, y }] });

test("textBoxFor: the box where the handwriting was, its size the handwriting's line height", () => {
  const strokes = [row("a", 200, 120), row("b", 230, 80), row("c", 260, 100)];
  const inkOf = groupsOf({ g: page(...strokes) });
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a", "b", "c"] }] }, inkOf);
  const [x0, y0, x1, y1] = req.box;
  const box = textBoxFor(req, "First line\nSecond, with $x^2$\n\n- third\n");
  const size = Math.round((y1 - y0) / 3 / LINE);
  assert.equal(size, 17);
  assert.deepEqual(box, normalizeTextBox({
    x: x0 - PAD, y: y0 - PAD, w: x1 - x0 + 2 * PAD, h: 3 * size * LINE + 2 * PAD, auto: true, size, color: "#1f1f1f", bg: null,
  }));
  assert.deepEqual([box.x, box.y, box.w, box.h, box.auto, box.bg], [95, 195, 130, 71.75, true, null]);
});

test("textBoxFor: a bare $$ is no line, a formula on one is; the size held to 6..96 pt", () => {
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a", "b"] }] }, groupsOf({ g: page(row("a", 100, 50), row("b", 159, 50)) }));
  const height = req.box[3] - req.box[1];
  assert.equal(textBoxFor(req, "Let\n$$\nx = 1\n$$\n").size, Math.round(height / 2 / LINE), "two lines: the $$ fences are not counted");
  assert.equal(textBoxFor(req, "Let\n  $$  \n$$x = 1$$").size, Math.round(height / 2 / LINE));
  assert.equal(textBoxFor(req, "").size, Math.round(height / LINE), "at least one line");
  assert.equal(textBoxFor(req, "").h, Math.round(height / LINE) * LINE + 2 * PAD);
  assert.equal(textBoxFor(req, Array(40).fill("x").join("\n")).size, 6);
  const tall = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a", "b"] }] }, groupsOf({ g: page(row("a", 100, 50), row("b", 400, 50)) }));
  assert.equal(textBoxFor(tall, "one").size, 96);
  assert.equal(textBoxFor({ ...req, box: null }, "x"), null);
  assert.equal(textBoxFor(null, "x"), null);
});

test("textBoxFor: the colour drawn longest, an rgb() one as hex; a highlighter's never; else the default", () => {
  const mixed = [row("a", 100, 30, "#1D4ED8"), row("b", 120, 30, "rgb(255, 0, 0)"), row("c", 140, 40, "rgba(255,0,0,0.5)"), highlighter("h", 0, 0)];
  const reqOf = (strokes) => inkAiRequest({ page: 1, items: [{ id: "g", ids: strokes.map((s) => s.id) }] }, groupsOf({ g: page(...strokes) }));
  assert.equal(textBoxFor(reqOf(mixed), "x").color, "#ff0000", "red is 70 pt in two strokes, blue 30");
  assert.equal(textBoxFor(reqOf([row("a", 100, 30, "#1D4ED8"), row("b", 120, 20, "rgb(255, 0, 0)")]), "x").color, "#1d4ed8");
  assert.equal(textBoxFor(reqOf([row("a", 100, 30, "#1d4ed8"), { ...highlighter("h"), color: "#00ff00", size: 2 }]), "x").color, "#1d4ed8");
  assert.equal(textBoxFor(reqOf([row("a", 100, 30, "not a colour")]), "x").color, "#1f1f1f");
  assert.equal(textBoxFor(reqOf([{ ...row("a", 100, 30), color: undefined }]), "x").color, "#1f1f1f");
});

test("textPlan: the request's pen strokes still in their groups removed, group by group", () => {
  const g1 = page(pen("a"), highlighter("h"), pen("b", 40), pen("keep", 80)), g2 = page(pen("x", 300));
  const req = inkAiRequest({ page: 1, items: [{ id: "g1", ids: ["a", "h", "b"] }, { id: "g2", ids: ["x"] }] }, groupsOf({ g1, g2 }));
  const plan = textPlan(req, groupsOf({ g1, g2 }));
  assert.equal(plan.removed, 3);
  assert.equal(plan.skipped, 0);
  assert.deepEqual(plan.changes.map((c) => [c.id, c.page, ids(c.after)]), [["g1", 1, ["h", "keep"]], ["g2", 1, []]]);
  assert.equal(plan.changes[0].before, g1);
  assert.deepEqual(plan.strokes, [g1.strokes[0], g1.strokes[2], g2.strokes[0]], "the strokes it removes, as they are");
  assert.deepEqual(plan.box, req.box, "nothing changed: the request's box");
  // meanwhile a was erased and g2 is gone: what is left of the request goes
  const later = textPlan(req, groupsOf({ g1: { ...g1, strokes: g1.strokes.slice(1) } }));
  assert.equal(later.removed, 1);
  assert.equal(later.skipped, 0, "erased and gone are not changed");
  assert.deepEqual(ids(later.changes[0].after), ["h", "keep"]);
  assert.deepEqual(later.box, strokeBounds(g1.strokes[2]));
  assert.deepEqual(textPlan(req, () => null), { changes: [], removed: 0, skipped: 0, box: null, strokes: [] });
});

test("textPlan: handwriting moved, resized or turned while the AI read it stays where it is now, counted as skipped", () => {
  const g = page(row("a", 200, 120), row("b", 230, 80));
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a", "b"] }] }, groupsOf({ g }));
  for (const [what, now] of [
    ["moved 200 pt down", translateStrokes(g, ["a", "b"], 0, 200)],
    ["resized", transformStrokes(g, ["a", "b"], { cx: 100, cy: 200, scale: 1.5 })],
    ["turned", transformStrokes(g, ["a", "b"], { cx: 150, cy: 215, angle: 90 })],
  ]) {
    const plan = textPlan(req, groupsOf({ g: now }));
    assert.deepEqual([plan.removed, plan.skipped, plan.changes, plan.box, plan.strokes], [0, 2, [], null, []], what);
  }
  // restyled only (the same samples): still the handwriting that was read
  const recoloured = restyleStrokes(g, ["a"], { color: "#0f766e" });
  assert.equal(textPlan(req, groupsOf({ g: recoloured })).removed, 2);
});

test("textPlan: some strokes moved meanwhile — the box goes on, and is sized by, the ones it removes", () => {
  const g = page(row("a", 200, 120), row("b", 230, 80), row("c", 400, 300, "#1d4ed8"));
  const req = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a", "b", "c"] }] }, groupsOf({ g }));
  const now = translateStrokes(g, ["c"], 0, 150);
  const plan = textPlan(req, groupsOf({ g: now }));
  assert.equal(plan.removed, 2);
  assert.equal(plan.skipped, 1);
  assert.deepEqual(ids(plan.changes[0].after), ["c"], "the moved stroke stays");
  assert.equal(plan.changes[0].before, now);
  const box = unionBox(strokeBounds(g.strokes[0]), strokeBounds(g.strokes[1]));
  assert.deepEqual(plan.box, box);
  assert.notDeepEqual(plan.box, req.box, "not the request's, which reached the moved stroke");
  const tb = textBoxFor(plan, "Two\nlines");
  assert.deepEqual([tb.x, tb.y, tb.w], [box[0] - PAD, box[1] - PAD, box[2] - box[0] + 2 * PAD]);
  assert.equal(tb.size, Math.round((box[3] - box[1]) / 2 / LINE), "the height of the two rows it replaces");
  assert.equal(textBoxFor(req, "x").color, "#1d4ed8", "(the request's longest colour is the moved stroke's)");
  assert.equal(tb.color, "#1f1f1f", "in the colour of the strokes it replaces, not the moved one's");
});

test("inkInTree: a group whose block left the tree is gone, though inkOf still has its draft; nothing is planned for it", () => {
  const g1 = page(pen("a")), g2 = page(pen("x", 300));
  const drafts = groupsOf({ g1, g2 });   // inkOf answers a draft whatever the tree holds
  const tree = [{ id: "sheet", properties: { sheet: {} }, children: [{ id: "g1", properties: { ink_url: "" }, children: [] }] }];
  const now = inkInTree(drafts, tree);
  assert.equal(now("g1"), g1, "a group under a sheet, at any depth");
  assert.equal(now("g2"), null, "a group deleted meanwhile");
  const req = inkAiRequest({ page: "sheet", items: [{ id: "g2", ids: ["x"] }] }, drafts);
  assert.deepEqual(redrawPlan(req, [{ kind: "rect", params: RECT, strokes: [0] }], now), { changes: [], items: [], count: 0, skipped: 1 },
    "skipped, so no edit puts its block back");
  assert.deepEqual(textPlan(req, now), { changes: [], removed: 0, skipped: 0, box: null, strokes: [] }, "gone: no text box either");
  assert.equal(inkInTree(drafts, [])("g1"), null, "the sheet deleted with it");
  const both = inkAiRequest({ page: "sheet", items: [{ id: "g1", ids: ["a"] }, { id: "g2", ids: ["x"] }] }, drafts);
  assert.deepEqual(textPlan(both, now).changes.map((c) => c.id), ["g1"], "the group still there is converted");
});

test("requestTooBig: past what the server reads as one file — its pen strokes, samples or bytes — highlighters not counted", () => {
  const small = inkAiRequest({ page: 1, items: [{ id: "g", ids: ["a", "h"] }] }, groupsOf({ g: page(pen("a"), highlighter("h")) }));
  assert.equal(requestTooBig(small), false);
  const tiny = (i, tool) => ({ id: `s${i}`, ch: "xy", pen: false, ...(tool ? { tool } : {}), pts: [i, 0, 100, 0] });
  const many = (n, tool) => ({ ...small, strokes: Array.from({ length: n }, (_, i) => tiny(i, tool)) });
  assert.equal(requestTooBig(many(MAX_STROKES)), false, "at the cap is fine");
  assert.equal(requestTooBig(many(MAX_STROKES + 1)), true, "two groups of 2,501 strokes each");
  assert.equal(requestTooBig(many(MAX_STROKES + 1, "highlighter")), false, "the server drops the highlighters before it parses");
  const samples = (n) => ({ ...small, strokes: [{ id: "s", ch: "xy", pen: false, pts: new Array(n * 2).fill(1) }] });
  assert.equal(requestTooBig(samples(MAX_SAMPLES)), false);
  assert.equal(requestTooBig(samples(MAX_SAMPLES + 1)), true);
  const wide = (n) => ({ ...small, strokes: [{ id: "s", ch: "xypt", pen: false, pts: new Array(n).fill(123456789) }] });
  const bytes = (n) => new TextEncoder().encode(JSON.stringify(wide(n).strokes)).length;
  assert.ok(bytes(700_000) > MAX_BYTES && 700_000 / 4 < MAX_SAMPLES, "past the bytes alone");
  assert.equal(requestTooBig(wide(700_000)), true);
  assert.ok(bytes(500_000) < MAX_BYTES - 4096);
  assert.equal(requestTooBig(wide(500_000)), false);
});

// --- the request (ink/inkAiClient.js), against a stubbed server ---------------------
// shared/lib/utils.js, behind the client, imports without extensions and
// reads `window` as it loads.
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    try { return await next(specifier, context); } catch (err) {
      if (err?.code === "ERR_MODULE_NOT_FOUND" && /^\\.\\.?\\//.test(specifier) && !/\\.[a-z]+$/.test(specifier)) return next(specifier + ".js", context);
      throw err;
    }
  }`));

async function client(t, answer) {
  globalThis.window = globalThis;
  globalThis.location = new URL("http://gamma.test/");
  globalThis.dispatchEvent = () => true;
  const asked = [];
  globalThis.fetch = async (url, init) => { asked.push({ url: String(url), init }); return answer(); };
  t.after(() => { delete globalThis.window; delete globalThis.location; delete globalThis.dispatchEvent; });
  const utils = await import("../src/shared/lib/utils.js");
  utils.setCurrentWorkspace("ws1");
  const { askInkAi } = await import("../src/ink/inkAiClient.js");
  return { askInkAi, asked };
}
const ndjson = (...lines) => new Response(lines.map((l) => JSON.stringify(l) + "\n").join(""), { status: 200 });

test("askInkAi: posts the body, skips the pings, resolves with the final line", async (t) => {
  let reply = () => ndjson({ ping: 1 }, { ping: 1 }, { shapes: [{ kind: "rect", params: RECT, strokes: [0] }] });
  const { askInkAi, asked } = await client(t, () => reply());
  const body = { space: { kind: "pdf-page", page: 1, width: 612, height: 792 }, strokes: [pen("a")] };
  const signal = new AbortController().signal;
  assert.deepEqual(await askInkAi("shapes", body, signal), { shapes: [{ kind: "rect", params: RECT, strokes: [0] }] });
  const [{ url, init }] = asked;
  assert.equal(new URL(url, "http://gamma.test/").pathname, "/api/ai/ink/shapes");
  assert.equal(init.method, "POST");
  assert.equal(init.credentials, "include");
  assert.equal(init.signal, signal);
  assert.equal(init.headers["Content-Type"], "application/json");
  assert.equal(init.headers["X-Gamma-Workspace"], "ws1", "the app's fetch wrapper adds the workspace");
  assert.deepEqual(JSON.parse(init.body), JSON.parse(JSON.stringify(body)));
  reply = () => ndjson({ ping: 1 }, { text: "Hello $x$" });
  assert.deepEqual(await askInkAi("text", body), { text: "Hello $x$" });
  assert.equal(new URL(asked[1].url, "http://gamma.test/").pathname, "/api/ai/ink/text");
  reply = () => ndjson({ ping: 1 });
  assert.equal(await askInkAi("text", body), null, "a stream that ended without its answer");
  await assert.rejects(askInkAi("svg", body), TypeError);
});

test("askInkAi: an error line throws its message; a refusal throws the server's detail with its status", async (t) => {
  let reply = () => ndjson({ ping: 1 }, { error: "redrawing failed: the AI's answer was not a list of shapes" });
  const { askInkAi } = await client(t, () => reply());
  await assert.rejects(askInkAi("shapes", {}), { message: "redrawing failed: the AI's answer was not a list of shapes" });
  reply = () => new Response(JSON.stringify({ detail: "Too much handwriting to redraw at once; select less." }), { status: 413 });
  await assert.rejects(askInkAi("shapes", {}), { message: "Too much handwriting to redraw at once; select less.", status: 413 });
  reply = () => new Response(JSON.stringify({ detail: "The shared AI allowance is used up." }), { status: 429, headers: { "Retry-After": "30" } });
  await assert.rejects(askInkAi("text", {}), (err) => err.status === 429 && err.data.detail.startsWith("The shared"));
  reply = () => new Response("Bad gateway", { status: 502 });
  await assert.rejects(askInkAi("text", {}), { message: "Bad gateway", status: 502 });
});
