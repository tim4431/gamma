// The ground under a block and the colour of its ink, read from a page's pixels
// (src/pdf/transPaint.js), and the store they are kept in (src/pdf/transFit.js).
import test from "node:test";
import assert from "node:assert/strict";
import { keepPaints, paintsOf } from "../src/pdf/transFit.js";
import { SAMPLE_SCALE, blockPaint, pagePaints, sampleScale } from "../src/pdf/transPaint.js";

const WIDTH = 200, HEIGHT = 100; // pt
const W = WIDTH * SAMPLE_SCALE, H = HEIGHT * SAMPLE_SCALE;

// A page of one colour, with what `paint` puts on it through set(x, y, rgb), in px.
function page(ground, paint = () => {}) {
  const px = new Uint8ClampedArray(W * H * 4);
  const set = (x, y, [r, g, b]) => {
    const at = (y * W + x) * 4;
    px[at] = r; px[at + 1] = g; px[at + 2] = b; px[at + 3] = 255;
  };
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) set(x, y, ground);
  paint(set);
  return px;
}

// A line of `count` strokes across a block's line: each `core` px of `ink` and an
// edge either side of `edge`, antialiased towards the ground, as text is.
const strokes = (set, { y1 = 40, y2 = 56, x1 = 40, count = 40, ink, edge = null, core = 2 }) => {
  for (let k = 0; k < count; k++) {
    const x = x1 + k * (core + 6);
    for (let y = y1; y < y2; y++) {
      for (let d = 0; d < core; d++) set(x + d, y, ink);
      if (edge) { set(x - 1, y, edge); set(x + core, y, edge); }
    }
  }
};

const block = { lines: [{ x1: 20, y1: 20, x2: 180, y2: 28 }] };
const BLACK = [0, 0, 0], WHITE = [255, 255, 255];

test("a block on plain paper in the usual ink has no paint, which most have", () => {
  assert.equal(blockPaint(page(WHITE, (set) => strokes(set, { ink: BLACK })), W, H, block), null);
  assert.equal(blockPaint(page([252, 252, 251], (set) => strokes(set, { ink: BLACK })), W, H, block), null, "white, nearly");
  assert.equal(blockPaint(page(WHITE, (set) => strokes(set, { ink: [40, 40, 40] })), W, H, block), null, "dark grey is the page's usual ink");
});

test("a tinted ground is the exact colour around the lines, whatever the text on it", () => {
  const paint = blockPaint(page([236, 236, 237], (set) => strokes(set, { ink: BLACK, edge: [120, 120, 120] })), W, H, block);
  assert.deepEqual(paint, { ground: "rgb(236 236 237)", ink: null });
});

test("the ground takes in its neighbours' noise and stays what most of it is", () => {
  const px = page([236, 236, 236], (set) => {
    for (let x = 30; x < 330; x += 2) set(x, 36, [233, 233, 234]);
    strokes(set, { ink: BLACK });
  });
  assert.equal(blockPaint(px, W, H, block).ground, "rgb(236 236 236)");
});

test("a heavy line's own ink does not pass for its ground: the ring around it is ground", () => {
  const px = page([230, 240, 250], (set) => strokes(set, { ink: BLACK, core: 10, count: 20 }));
  assert.equal(blockPaint(px, W, H, block).ground, "rgb(230 240 250)", "ink fills 62% of the line");
  const paper = page(WHITE, (set) => strokes(set, { ink: BLACK, core: 10, count: 20 }));
  assert.equal(blockPaint(paper, W, H, block), null);
});

test("over a gradient the text's own colour is no ground: the block has no paint", () => {
  const px = page(WHITE, (set) => {
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const v = 30 + ((x + y * 7) % 200); set(x, y, [v, 255 - v, (v * 3) % 256]); }
    strokes(set, { ink: BLACK, core: 3 });
  });
  assert.equal(blockPaint(px, W, H, block), null, "not a black mask under dark text");
});

test("a band no taller than its line is the ground though the ring above and below is paper", () => {
  const green = [51, 153, 76];
  const px = page(WHITE, (set) => {
    for (let y = 41; y < 55; y++) for (let x = 0; x < W; x++) set(x, y, green); // inside the line's box, past its ends
    strokes(set, { ink: WHITE, core: 3 });
  });
  assert.deepEqual(blockPaint(px, W, H, block), { ground: "rgb(51 153 76)", ink: "rgb(255 255 255)" });
});

test("a yellow that outweighs the text on paper is no ink: its contrast would leave nothing to read", () => {
  const px = page(WHITE, (set) => {
    strokes(set, { ink: [255, 212, 0], core: 5, count: 25 });
    strokes(set, { ink: BLACK, core: 1, x1: 43, count: 40 });
  });
  assert.equal(blockPaint(px, W, H, block), null);
  const blue = page(WHITE, (set) => strokes(set, { ink: [255, 80, 0], core: 3 }));
  assert.equal(blockPaint(blue, W, H, block).ink, "rgb(255 80 0)", "an orange heading keeps its colour");
});

test("a dark ground with no ink of its own is no ground: the usual ink could not be read on it", () => {
  assert.equal(blockPaint(page([20, 20, 30]), W, H, block), null);
});

test("a page is read at the scale it can afford, no finer than its bitmap", () => {
  assert.equal(sampleScale(612, 792), SAMPLE_SCALE);
  assert.equal(sampleScale(612, 792, 1.3), 1.3, "the bitmap holds no more");
  assert.equal(sampleScale(612, 792, 3), SAMPLE_SCALE, "and a sharper one takes no more");
  const k = sampleScale(14400, 14400, 2);
  assert.ok(Math.ceil(14400 * k) ** 2 <= 4_001_000, `${k}`);
});

test("a page read coarser comes to the same", () => {
  const k = 1, w = WIDTH * k, h = HEIGHT * k;
  const px = new Uint8ClampedArray(w * h * 4).fill(255);
  for (let at = 0; at < px.length; at += 4) { px[at] = 236; px[at + 1] = 236; px[at + 2] = 237; }
  for (let x = 30; x < 170; x += 6) for (let y = 21; y < 27; y++) { const at = (y * w + x) * 4; px[at] = 0; px[at + 1] = 0; px[at + 2] = 0; }
  assert.deepEqual(blockPaint(px, w, h, block, k), { ground: "rgb(236 236 237)", ink: null });
  const paras = [{ ...block, translate: true }];
  assert.deepEqual([...pagePaints(paras, px, w, h, k)], [[0, { ground: "rgb(236 236 237)", ink: null }]]);
});

test("ink is what lies farthest from the ground, past its antialiased edges", () => {
  const blue = [0, 60, 200];
  const paint = blockPaint(page(WHITE, (set) => strokes(set, { ink: blue, edge: [128, 158, 228], core: 1 })), W, H, block);
  assert.deepEqual(paint, { ground: null, ink: "rgb(0 60 200)" });
});

test("the mode wins among the farthest: a link in a black paragraph leaves it the usual ink", () => {
  const px = page(WHITE, (set) => {
    strokes(set, { ink: BLACK, count: 30 });
    strokes(set, { ink: [0, 0, 255], x1: 290, count: 4 });
  });
  assert.equal(blockPaint(px, W, H, block), null);
});

test("a mode short of a majority says nothing: black text thick with blue citations is no blue block", () => {
  const px = page(WHITE, (set) => {
    strokes(set, { ink: [45, 48, 146], count: 18 });
    strokes(set, { ink: [10, 8, 60 ], x1: 190, count: 8 });
    strokes(set, { ink: [40, 90, 30], x1: 250, count: 8 });
    strokes(set, { ink: [90, 30, 30], x1: 310, count: 3 });
  });
  assert.equal(blockPaint(px, W, H, block), null);
});

test("white on a dark ground is an ink of its own, a grey on a light one is thin black at a low zoom", () => {
  const dark = blockPaint(page([53, 58, 145], (set) => strokes(set, { ink: WHITE })), W, H, block);
  assert.deepEqual(dark, { ground: "rgb(53 58 145)", ink: "rgb(255 255 255)" });
  assert.equal(blockPaint(page(WHITE, (set) => strokes(set, { ink: [150, 150, 150] })), W, H, block), null);
  assert.equal(blockPaint(page([236, 236, 236], (set) => strokes(set, { ink: [170, 170, 170] })), W, H, block).ink, null);
});

test("a line at the page's edge reads what the page holds of it", () => {
  const edge = { lines: [{ x1: -10, y1: 95, x2: 300, y2: 120 }] };
  assert.deepEqual(blockPaint(page([200, 220, 240]), W, H, edge), { ground: "rgb(200 220 240)", ink: null });
  assert.equal(blockPaint(page(WHITE), W, H, { lines: [{ x1: 400, y1: 400, x2: 500, y2: 410 }] }), null, "nothing of it on the page");
  assert.equal(blockPaint(page(WHITE), W, H, { lines: [] }), null);
});

test("a long paragraph is read as thinly as a short one, and comes to the same", () => {
  const lines = Array.from({ length: 12 }, (_, i) => ({ x1: 10, y1: 2 + i * 8, x2: 190, y2: 9 + i * 8 }));
  const px = page([236, 236, 237], (set) => { for (let i = 0; i < 12; i++) strokes(set, { ink: BLACK, y1: 6 + i * 16, y2: 16 + i * 16, x1: 24, count: 44 }); });
  assert.deepEqual(blockPaint(px, W, H, { lines }), { ground: "rgb(236 236 237)", ink: null });
});

test("a page's paints are its translated blocks that have one, by index", () => {
  const px = page(WHITE, (set) => { for (let y = 0; y < 60; y++) for (let x = 0; x < W; x++) set(x, y, [236, 236, 236]); });
  const tinted = { translate: true, lines: [{ x1: 20, y1: 5, x2: 180, y2: 12 }] };
  const paper = { translate: true, lines: [{ x1: 20, y1: 60, x2: 180, y2: 70 }] };
  const figure = { translate: false, lines: tinted.lines };
  assert.deepEqual([...pagePaints([paper, figure, tinted], px, W, H)], [[2, { ground: "rgb(236 236 236)", ink: null }]]);
});

test("paints live with the page's blocks, read once, an empty read too", () => {
  const paras = [{}], other = [{}];
  assert.equal(paintsOf(paras), null, "nothing read yet");
  keepPaints(paras, new Map());
  assert.equal(paintsOf(paras).size, 0, "a page with none has been read");
  assert.equal(paintsOf(other), null, "each page's own");
});
