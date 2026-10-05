import test from "node:test";
import assert from "node:assert/strict";
import { basePlan, windowMargin, windowSlice, detailPlan, covers } from "../src/pdf/pageRaster.js";
import { canvasSize, CANVAS_MAX_PIXELS, CANVAS_MAX_EDGE } from "../src/shared/lib/canvasSize.js";

const inLimits = ({ width, height }) =>
  width * height <= CANVAS_MAX_PIXELS && width <= CANVAS_MAX_EDGE && height <= CANVAS_MAX_EDGE;
const near = (a, b) => Math.abs(a - b) < 1e-6;

test("at ordinary zooms the whole page is one supersampled canvas", () => {
  for (const dpr of [1, 1.5, 2]) assert.deepEqual(basePlan(612, 792, dpr), { width: 1224, height: 1584, detail: false });
  assert.deepEqual(basePlan(612, 792, 3), { width: 1836, height: 2376, detail: false });
  assert.deepEqual(basePlan(612, 792, 5), { width: 1836, height: 2376, detail: false }, "never past 3 px per CSS px");
});

test("detail mode starts exactly when the capped canvas falls below the screen's ratio", () => {
  assert.equal(basePlan(2448, 3168, 1).detail, false, "Letter at 400% still fits at DPR 1");
  assert.equal(basePlan(2448, 3168, 1.5).detail, true);
  assert.equal(basePlan(2448, 3168, 2).detail, true);
  assert.equal(basePlan(765, 990, 3).detail, false);
  assert.equal(basePlan(765, 990, 3.5).detail, false, "a DPR above 3 asks for no more than 3");
});

test("in detail mode the base has a quarter of the pixels the limits allow", () => {
  const cap = canvasSize(2448, 3168, 2), base = basePlan(2448, 3168, 2);
  assert(base.detail);
  assert(Math.abs(base.width - cap.width / 2) <= 1 && Math.abs(base.height - cap.height / 2) <= 1);
  assert.deepEqual(basePlan(2448, 3168, 3), base, "the screen ratio does not change the base once in detail");
});

test("the base stays inside both canvas limits and degenerate boxes never ask for detail", () => {
  for (const [w, h] of [[612, 792], [2448, 3168], [4896, 6336], [10000, 14000], [400, 50000], [50000, 400]]) {
    for (const dpr of [1, 1.5, 2, 3, 4]) assert(inLimits(basePlan(w, h, dpr)), `${w}x${h}@${dpr}`);
  }
  for (const [w, h] of [[0, 792], [612, 0], [NaN, 792], [-612, 792], [612, Infinity]]) {
    assert.deepEqual(basePlan(w, h, 2), { width: 1, height: 1, detail: false });
  }
  assert.equal(basePlan(612, 792, 0).detail, false, "a missing DPR counts as 1");
});

test("the window margin stays between 0 and one half, and the grown viewport inside the pixel budget", () => {
  for (const [w, h] of [[390, 700], [390, 844], [1024, 700], [1400, 900], [1920, 1080], [2560, 1300], [200, 150]]) {
    for (const dpr of [1, 1.5, 2, 3, 4]) {
      const m = windowMargin(w, h, dpr), s = Math.min(3, dpr);
      assert(m >= 0 && m <= 0.5, `${w}x${h}@${dpr}: ${m}`);
      if (m > 0) assert(w * (1 + 2 * m) * h * (1 + 2 * m) * s * s <= CANVAS_MAX_PIXELS * (1 + 1e-12));
    }
  }
  assert.equal(windowMargin(200, 150, 1), 0.5);
});

test("no margin when the viewport alone uses the budget, or has no size", () => {
  assert.equal(windowMargin(2560, 1300, 2), 0);
  assert.equal(windowMargin(3000, 3000, 1), 0);
  assert.equal(windowMargin(0, 900, 2), 0);
  assert.equal(windowMargin(1400, NaN, 2), 0);
});

test("a long narrow viewport keeps the screen's ratio: the margin stops at the edge limit too", () => {
  for (const [w, h, dpr] of [[390, 844, 3], [430, 932, 3], [1920, 800, 2], [2400, 700, 1.5]]) {
    const view = { x: 3000, y: 3000, width: w, height: h };
    const plan = detailPlan(windowSlice(20000, 20000, view, windowMargin(w, h, dpr)), dpr);
    assert(near(plan.width / plan.rect.width, dpr), `${w}x${h} @${dpr}: ${plan.width / plan.rect.width}`);
    assert(inLimits(plan));
  }
});

test("the window slice is the grown viewport clipped to the page", () => {
  const view = { x: 1000, y: 2000, width: 1000, height: 500 };
  assert.deepEqual(windowSlice(4896, 6336, view, 0), view);
  assert.deepEqual(windowSlice(4896, 6336, view, 0.1), { x: 900, y: 1950, width: 1200, height: 600 });
  assert.deepEqual(windowSlice(4896, 6336, { x: -300, y: -100, width: 1000, height: 500 }, 0.1), { x: 0, y: 0, width: 800, height: 450 });
  assert.deepEqual(windowSlice(4896, 6336, { x: 4500, y: 6000, width: 1000, height: 500 }, 0), { x: 4500, y: 6000, width: 396, height: 336 });
});

test("a page outside the grown viewport has no slice, one just inside its margin has one", () => {
  const below = { x: 0, y: 6436, width: 1000, height: 500 };
  assert.equal(windowSlice(4896, 6336, below, 0), null);
  assert.equal(windowSlice(4896, 6336, below, 0.1), null, "50 px of margin does not reach 100 px back");
  assert.deepEqual(windowSlice(4896, 6336, below, 0.3), { x: 0, y: 6286, width: 1300, height: 50 });
  assert.equal(windowSlice(4896, 6336, { x: 4896, y: 0, width: 1000, height: 500 }, 0), null, "touching is not meeting");
});

test("an ordinary slice is drawn at the screen's ratio, on the page's pixel grid, within 2 px of the slice", () => {
  for (const dpr of [1, 1.5, 2, 3]) {
    for (const slice of [{ x: 1000.3, y: 2000.7, width: 1200.4, height: 700.2 }, { x: 13.37, y: 0, width: 333.3, height: 999.9 }]) {
      const { width, height, rect } = detailPlan(slice, dpr);
      assert.equal(width, Math.floor(slice.width * dpr));
      assert.equal(height, Math.floor(slice.height * dpr));
      assert(near(rect.x * dpr, Math.round(rect.x * dpr)) && near(rect.y * dpr, Math.round(rect.y * dpr)));
      assert(near(rect.width * dpr, width) && near(rect.height * dpr, height));
      for (const [a, b] of [[rect.x, slice.x], [rect.y, slice.y],
        [rect.x + rect.width, slice.x + slice.width], [rect.y + rect.height, slice.y + slice.height]]) assert(Math.abs(a - b) < 2);
    }
  }
});

test("a huge slice is drawn inside both canvas limits", () => {
  for (const slice of [{ x: 0, y: 0, width: 5000, height: 4000 }, { x: 10, y: 10, width: 9000, height: 500 }, { x: 0, y: 0, width: 20000, height: 20000 }]) {
    for (const dpr of [1, 2, 3]) {
      const plan = detailPlan(slice, dpr), ratio = plan.width / plan.rect.width;
      assert(inLimits(plan), JSON.stringify({ slice, dpr }));
      assert(ratio < dpr && near(plan.rect.height * ratio, plan.height));
      assert(near(plan.rect.x * ratio, Math.round(plan.rect.x * ratio)));
    }
  }
});

test("covers takes containment and the pixel-grid rounding, not a rectangle missing more than that", () => {
  const need = { x: 100, y: 200, width: 300, height: 400 };
  assert(covers({ x: 50, y: 150, width: 400, height: 500 }, need));
  assert(covers(need, need));
  assert(covers({ x: 101.5, y: 201.5, width: 297, height: 397 }, need), "1.5 px short on every side");
  for (const slice of [need, { x: 1000.3, y: 2000.7, width: 1200.4, height: 700.2 }]) {
    for (const dpr of [1, 1.5, 2, 3]) assert(covers(detailPlan(slice, dpr).rect, slice));
  }
  assert(!covers({ x: 103, y: 200, width: 297, height: 400 }, need), "left");
  assert(!covers({ x: 100, y: 203, width: 300, height: 397 }, need), "top");
  assert(!covers({ x: 100, y: 200, width: 297, height: 400 }, need), "right");
  assert(!covers({ x: 100, y: 200, width: 300, height: 397 }, need), "bottom");
});

test("for realistic views the planned detail canvas covers what is on screen and stays in limits", () => {
  let checked = 0;
  for (const [pw, ph] of [[612, 792], [595.28, 841.89]]) {
    for (const zoom of [2, 3, 4, 5.5, 8]) {
      const W = pw * zoom, H = ph * zoom;
      for (const [vw, vh] of [[1400, 900], [1024, 700], [390, 700], [2560, 1300]]) {
        for (const dpr of [1, 1.5, 2, 3]) {
          const margin = windowMargin(vw, vh, dpr);
          for (const fx of [-0.3, 0, 0.37, 0.8, 1.1]) {
            for (const fy of [-0.3, 0, 0.51, 0.93, 1.1]) {
              const view = { x: fx * W - vw / 2, y: fy * H - vh / 2, width: vw, height: vh };
              const bare = windowSlice(W, H, view, 0);
              if (!bare) continue;
              const slice = windowSlice(W, H, view, margin);
              const plan = detailPlan(slice, dpr);
              const at = JSON.stringify({ W, H, view, dpr });
              assert(covers(plan.rect, bare), at);
              assert(covers(plan.rect, slice), at);
              assert(inLimits(plan), at);
              checked++;
            }
          }
        }
      }
    }
  }
  assert(checked > 1000);
});
