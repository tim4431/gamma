// node --test tests/inkSvg.test.mjs (from frontend/) — Copy as SVG and
// Download SVG's document (ink/inkSvg.js): the strokes' box grown by the
// pad, the layer's draw order (its planes), and each stroke drawn from
// strokePath with the attributes InkLayer.jsx Strokes gives it: a pen's
// filled outline, a dashed pen's and a highlighter's stroked line (the
// highlighter multiplied), a pencil's shade bands, a closed shape's fill
// under its line; the stored colours, opacities only below 1, values
// escaped; a transparent ground, or dark paper's under the strokes with the
// highlighters unblended. jsdom parses the markup as a browser would.
import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";
import { FILL_OPACITY, encodeStroke, strokeBounds, strokePath, unionBox } from "../src/ink/ink.js";
import { shapeStroke } from "../src/ink/inkAi.js";
import { inkSvg } from "../src/ink/inkSvg.js";

const { DOMParser } = new JSDOM("").window;
const parse = (markup) => {
  const doc = new DOMParser().parseFromString(markup, "image/svg+xml");
  assert.equal(doc.getElementsByTagName("parsererror").length, 0, "well-formed XML");
  return doc.documentElement;
};
const paths = (root) => [...root.getElementsByTagName("path")];
const attrs = (el) => Object.fromEntries([...el.attributes].map((a) => [a.name, a.value]));

const SAMPLES = [{ x: 10, y: 20, p: 0.5 }, { x: 30, y: 25, p: 0.6 }, { x: 60, y: 22, p: 0.5 }];
const pen = (over = {}) => encodeStroke({ id: "p", ch: "xyp", size: 2, samples: SAMPLES, ...over });
const highlighter = (over = {}) => encodeStroke({ id: "h", tool: "highlighter", ch: "xy", pen: false, size: 10, opacity: 0.6, color: "#ffe28f",
  samples: [{ x: 0, y: 40 }, { x: 80, y: 40 }], ...over });

test("a pen stroke: a document around its box grown by the pad, its filled outline in its colour", () => {
  const s = pen({ color: "#1d4ed8" });
  const out = inkSvg([s]);
  const [x0, y0, x1, y1] = strokeBounds(s);
  const root = parse(out.markup);
  assert.equal(root.localName, "svg");
  assert.equal(root.namespaceURI, "http://www.w3.org/2000/svg");
  const left = Math.floor((x0 - 4) * 100) / 100, top = Math.floor((y0 - 4) * 100) / 100;
  assert.equal(out.width, Math.ceil((x1 + 4 - left) * 100) / 100);
  assert.equal(out.height, Math.ceil((y1 + 4 - top) * 100) / 100);
  assert.ok(out.width >= x1 - x0 + 8 && out.width < x1 - x0 + 8.03, "the box grown by 4 on each side, up to the 1/100 grid");
  assert.equal(root.getAttribute("width"), String(out.width));
  assert.equal(root.getAttribute("height"), String(out.height));
  assert.equal(root.getAttribute("viewBox"), `0 0 ${out.width} ${out.height}`);
  const g = root.getElementsByTagName("g")[0];
  assert.equal(g.getAttribute("transform"), `translate(${-left} ${-top})`, "the box's corner at the document's");
  const [line] = paths(root);
  assert.deepEqual(attrs(line), { d: strokePath(s).d, fill: "#1d4ed8" }, "filled, no opacity at 1");
  assert.equal(paths(parse(inkSvg([pen({ opacity: 0.5 })]).markup))[0].getAttribute("fill-opacity"), "0.5");
});

test("a stroke without a colour in the first ink colour", () => {
  const out = inkSvg([pen({ color: undefined })]);
  assert.equal(paths(parse(out.markup))[0].getAttribute("fill"), "#1f1f1f");
});

test("a dashed pen: its centreline stroked at its size with the dash pattern, round caps and joins", () => {
  const s = encodeStroke({ id: "d", ch: "xy", pen: false, size: 2, dash: "dashed", color: "#15803d", samples: [{ x: 0, y: 0 }, { x: 50, y: 0 }] });
  const [line] = paths(parse(inkSvg([s]).markup));
  assert.deepEqual(attrs(line), {
    d: strokePath(s).d, fill: "none", stroke: "#15803d", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round",
    "stroke-dasharray": "4 4",
  });
  const dotted = paths(parse(inkSvg([{ ...s, dash: "dotted" }]).markup))[0];
  assert.equal(dotted.getAttribute("stroke-dasharray"), strokePath({ ...s, dash: "dotted" }).dash);
});

test("a highlighter: its polyline stroked at its size, at its opacity, multiplied", () => {
  const s = highlighter();
  const [line] = paths(parse(inkSvg([s]).markup));
  assert.deepEqual(attrs(line), {
    d: strokePath(s).d, fill: "none", stroke: "#ffe28f", "stroke-width": "10", "stroke-opacity": "0.6",
    "stroke-linecap": "round", "stroke-linejoin": "round", style: "mix-blend-mode:multiply",
  });
});

test("a pencil: a path per shade band at the band's alpha × its opacity, in its plain colour (no grain)", () => {
  const samples = Array.from({ length: 21 }, (_, i) => ({ x: i * 4, y: 10 + (i % 3), p: 0.5, o: i < 7 ? 0.2 : i < 14 ? 0.5 : 1 }));
  const s = encodeStroke({ id: "c", ch: "xypo", brush: "pencil", size: 2, opacity: 0.8, color: "#6b7280", samples });
  const { bands } = strokePath(s);
  assert.equal(bands.length, 3, "the stroke has three shades");
  const drawn = paths(parse(inkSvg([s]).markup));
  assert.deepEqual(drawn.map((p) => p.getAttribute("d")), bands.map((b) => b.d));
  assert.deepEqual(drawn.map((p) => p.getAttribute("fill-opacity")), bands.map((b) => String(Math.round(b.alpha * 0.8 * 1000) / 1000)));
  assert.ok(drawn.every((p) => p.getAttribute("fill") === "#6b7280"), "the plain colour, never a url(#grain)");
  const whole = paths(parse(inkSvg([{ ...s, opacity: 1 }]).markup));
  assert.equal(whole.at(-1).hasAttribute("fill-opacity"), false, "the full band at opacity 1 has none");
});

test("a filled closed shape: its inside first, at the fill's opacity, then its line", () => {
  const s = shapeStroke("rect", { cx: 50, cy: 50, w: 40, h: 20, angle: 0 }, { color: "#1f1f1f", size: 2, fill: { color: "#ff0000" } });
  const [fill, line, more] = paths(parse(inkSvg([s]).markup));
  assert.equal(more, undefined);
  assert.deepEqual(attrs(fill), { d: strokePath(s).fill, fill: "#ff0000", "fill-opacity": String(FILL_OPACITY) });
  assert.deepEqual(attrs(line), { d: strokePath(s).d, fill: "#1f1f1f" });
  const opaque = paths(parse(inkSvg([{ ...s, fill: { color: "#ff0000", opacity: 1 } }]).markup))[0];
  assert.equal(opaque.hasAttribute("fill-opacity"), false);
  const open = paths(parse(inkSvg([{ ...s, shape: { kind: "line", params: {} } }]).markup));
  assert.equal(open.length, 1, "a fill on a kind without an inside is not drawn");
});

// A drawn path's paint: its stroke for a stroked line, else its fill.
const paint = (p) => (p.getAttribute("fill") === "none" ? p.getAttribute("stroke") : p.getAttribute("fill"));

test("draw order: the layer's planes — band 0's other strokes, band 0's highlighters, band 1's others, band 1's highlighters — each in the order given", () => {
  const a = pen({ id: "a", color: "#000001" }), b = pen({ id: "b", color: "#000002" });
  const h = highlighter({ id: "h" }), under = pen({ id: "u", color: "#000003", band: 0 });
  // a pen set under (an imported sticky) lies beneath a band-0 highlighter given before it, as on the page
  assert.deepEqual(paths(parse(inkSvg([a, h, b, under]).markup)).map(paint), ["#000003", "#ffe28f", "#000001", "#000002"]);
  // a highlighter set over the ink is drawn after the pens, even given before them
  const over = highlighter({ id: "o", color: "#00ff00", band: 1 });
  assert.deepEqual(paths(parse(inkSvg([over, a, h, b]).markup)).map(paint), ["#ffe28f", "#000001", "#000002", "#00ff00"]);
  const out = inkSvg([a, h]);
  const box = unionBox(strokeBounds(a), strokeBounds(h));
  assert.ok(out.width >= box[2] - box[0] + 8 && out.height >= box[3] - box[1] + 8, "the box is every stroke's");
});

test("a background: a rect of it first, the document's size, under the strokes, and a highlighter without its blend", () => {
  const s = pen({ color: "#ffffff" }), h = highlighter();
  const plain = inkSvg([s, h]), dark = inkSvg([s, h], { background: "#2b2d31" });
  assert.deepEqual([dark.width, dark.height], [plain.width, plain.height], "the same document size");
  const root = parse(dark.markup);
  const first = root.firstElementChild;
  assert.equal(first.localName, "rect", "the ground first, outside the translated group");
  assert.deepEqual(attrs(first), { width: String(dark.width), height: String(dark.height), fill: "#2b2d31" });
  assert.equal(root.getElementsByTagName("rect").length, 1);
  // (the highlighter's plane is under the ink's)
  const [line, ink] = paths(root);
  assert.deepEqual(attrs(ink), attrs(paths(parse(plain.markup))[1]), "the ink as it is");
  assert.equal(line.hasAttribute("style"), false, "multiplied into dark paper, a highlighter would all but vanish");
  assert.equal(line.getAttribute("stroke"), "#ffe28f");
  // escaped as every attribute is
  assert.equal(parse(inkSvg([s], { background: `#000" onload="x()` }).markup).firstElementChild.getAttribute("fill"), `#000" onload="x()`);
});

test("no background: no ground, and the markup as without the option", () => {
  const strokes = [pen({ color: "#1d4ed8" }), highlighter()];
  const plain = inkSvg(strokes);
  assert.equal(inkSvg(strokes, { background: null }).markup, plain.markup);
  assert.equal(inkSvg(strokes, {}).markup, plain.markup);
  const root = parse(plain.markup);
  assert.equal(root.getElementsByTagName("rect").length, 0, "a transparent ground");
  assert.equal(root.firstElementChild.localName, "g");
  assert.equal(paths(root)[0].getAttribute("style"), "mix-blend-mode:multiply", "the highlighter, multiplied");
});

test("attribute values are escaped: the markup parses and reads back what the stroke holds", () => {
  const color = `#1f1f1f" onload="x()<&>'`;
  const out = inkSvg([pen({ color })]);
  assert.ok(!out.markup.includes('" onload="'), "no attribute breaks out");
  const [line] = paths(parse(out.markup));
  assert.equal(line.getAttribute("fill"), color);
  assert.equal(line.hasAttribute("onload"), false);
});

test("no strokes, or none with a sample: null", () => {
  assert.equal(inkSvg([]), null);
  assert.equal(inkSvg(null), null);
  assert.equal(inkSvg([{ id: "e", ch: "xy", pts: [] }]), null);
  const kept = inkSvg([{ id: "e", ch: "xy", pts: [] }, pen()]);
  assert.equal(paths(parse(kept.markup)).length, 1, "a stroke with no sample is left out");
});
