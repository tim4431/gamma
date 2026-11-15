import test from "node:test";
import assert from "node:assert/strict";
import { ACROSS, PAGE_GAP, crossStart, pageAt, pageInMiddle, pageLanded, pageLanding, pageStartAt, runsAcross } from "../src/pdf/pageLayout.js";

const sizes = [600, 400, 600];

test("along the run: each page starts after the ones before it and their gaps, at any zoom", () => {
  assert.equal(pageStartAt(sizes, 0, 1, 500), 0);
  assert.equal(pageStartAt(sizes, 2, 1, 500), 1000 + 2 * PAGE_GAP);
  assert.equal(pageStartAt(sizes, 2, 2, 500), 2000 + 2 * PAGE_GAP, "the gaps do not scale");
  assert.equal(pageStartAt([600, 0], 2, 1, 500), 1100 + 2 * PAGE_GAP, "a page not measured counts as the fallback");
});

test("the page under a run position: how far into it, and how far into the gap after it", () => {
  assert.deepEqual(pageAt(sizes, 1, 300, 500), { idx: 0, frac: 0.5, gap: 0 });
  assert.deepEqual(pageAt(sizes, 1, 603, 500), { idx: 0, frac: 1, gap: 3 }, "the gap after a page is that page's");
  assert.deepEqual(pageAt(sizes, 2, 1200 + PAGE_GAP + 400, 500), { idx: 1, frac: 0.5, gap: 0 });
  assert.deepEqual(pageAt(sizes, 1, 99999, 500), { idx: 2, frac: 1, gap: 0 }, "past the end: the last page");
  assert.equal(crossStart(1000, 600), 200, "a page shorter than its line is centred in it");
  assert.equal(crossStart(1000, 1600), 0, "a longer one starts at the line's start");
});

// A scroller 1000 × 800 at the origin with three page boxes laid out as
// `rects` (in the view, after `scroll`).
function scroller({ across = false, scroll = 0, range = 4000, rects }) {
  const el = {
    dataset: across ? { flow: ACROSS } : {}, clientLeft: 0, clientTop: 0, clientWidth: 1000, clientHeight: 800,
    scrollLeft: across ? scroll : 0, scrollTop: across ? 0 : scroll,
    scrollWidth: across ? range : 1000, scrollHeight: across ? 800 : range,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 800, right: 1000, bottom: 800 }),
  };
  const boxes = rects.map(([left, top, width, height]) => ({
    getBoundingClientRect: () => ({ left, top, width, height, right: left + width, bottom: top + height }),
  }));
  return { el, boxes };
}

test("a jump lands a page 80 px in, or across, a page the view holds whole in its middle", () => {
  assert.equal(runsAcross(scroller({ across: true, rects: [] }).el), true);
  assert.equal(runsAcross(scroller({ rects: [] }).el), false);
  const down = scroller({ scroll: 500, rects: [[200, -500, 600, 900], [200, 408, 600, 900]] });
  assert.deepEqual(pageLanding(down.el, down.boxes[1]), { top: 500 + 408 - 80 });
  assert.equal(pageLanded(down.el, down.boxes[1]), false);
  const landed = scroller({ scroll: 828, rects: [[200, -828, 600, 900], [200, 80, 600, 900]] });
  assert.equal(pageLanded(landed.el, landed.boxes[1]), true);

  const across = scroller({ across: true, scroll: 300, rects: [[-300, 100, 600, 600], [308, 100, 600, 600]] });
  assert.deepEqual(pageLanding(across.el, across.boxes[1]), { left: 300 + 308 - 200 }, "centred: (1000 - 600) / 2 in");
  assert.equal(pageLanded(across.el, across.boxes[1]), true, "208 px off the middle's 200 is within the slack");
  const wide = scroller({ across: true, scroll: 0, rects: [[0, 0, 1600, 800], [1608, 0, 1600, 800]] });
  assert.deepEqual(pageLanding(wide.el, wide.boxes[1]), { left: 1608 - 80 }, "wider than the view: 80 px in");
  // The first page cannot be centred in a view that starts on it.
  const first = scroller({ across: true, scroll: 0, rects: [[0, 100, 500, 600]] });
  assert.equal(pageLanded(first.el, first.boxes[0]), true, "at the start, as far as it goes");
  // A short last page at the end cannot come up to 80 px under the top.
  const last = scroller({ scroll: 3200, range: 4000, rects: [[200, -400, 600, 900], [200, 508, 600, 300]] });
  assert.equal(pageLanded(last.el, last.boxes[1]), true, "at the end, as far as it goes");
  const short = scroller({ scroll: 2000, range: 4000, rects: [[200, 508, 600, 300]] });
  assert.equal(pageLanded(short.el, short.boxes[0]), false, "not at the end: it can come further");
});

test("the page under the middle of the view, down or across", () => {
  const down = scroller({ rects: [[0, -900, 600, 800], [0, -92, 600, 800], [0, 716, 600, 800]] });
  assert.equal(pageInMiddle(down.el, down.boxes), down.boxes[1]);
  const across = scroller({ across: true, rects: [[-400, 0, 600, 800], [208, 0, 600, 800], [816, 0, 600, 800]] });
  assert.equal(pageInMiddle(across.el, across.boxes), across.boxes[1]);
  const gap = scroller({ across: true, rects: [[-104, 0, 600, 800], [504, 0, 600, 800]] });
  assert.equal(pageInMiddle(gap.el, gap.boxes), null, "the middle in a gap: none");
});
