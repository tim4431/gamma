// node --test tests/pageScale.test.mjs (from frontend/) — the scale a
// viewer's pages are drawn at, live and settled (pdf/pageScale.js;
// docs/dev/pdf_loading.md "High zoom and touch scrolling").
import assert from "node:assert/strict";
import { mock, test } from "node:test";
import { createPageScale } from "../src/pdf/pageScale.js";
import { ZOOM_SETTLE_MS } from "../src/pdf/pageRaster.js";

test("the live value follows set at once; an unchanged one notifies nothing", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const zoom = createPageScale(1);
    let heard = 0;
    zoom.subscribe(() => { heard++; });
    assert.equal(zoom.get(), 1);
    zoom.set(1);
    assert.equal(heard, 0, "the same value is no change");
    zoom.set(1.25);
    assert.equal(zoom.get(), 1.25);
    assert.equal(zoom.rest(), 1, "the settled value waits");
    assert.equal(heard, 1);
    zoom.set(1.25);
    assert.equal(heard, 1);
    mock.timers.tick(ZOOM_SETTLE_MS * 2);
    assert.equal(heard, 2, "and once more when it settles");
  } finally {
    mock.timers.reset();
  }
});

test("rest moves ZOOM_SETTLE_MS after the last set, once per train", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const zoom = createPageScale(1);
    const rests = [];
    zoom.subscribe(() => { if (rests.at(-1) !== zoom.rest()) rests.push(zoom.rest()); });
    for (const s of [1.1, 1.2, 1.3, 1.4]) {
      zoom.set(s);
      mock.timers.tick(ZOOM_SETTLE_MS - 10);
      assert.equal(zoom.rest(), 1, `still 1 while the train runs (at ${s})`);
    }
    mock.timers.tick(10);
    assert.equal(zoom.rest(), 1.4);
    assert.deepEqual(rests, [1, 1.4], "one settled move for the train");
    // A train that ends where it started moves nothing.
    let heard = 0;
    zoom.subscribe(() => { heard++; });
    zoom.set(1.5);
    zoom.set(1.4);
    mock.timers.tick(ZOOM_SETTLE_MS);
    assert.equal(zoom.rest(), 1.4);
    assert.equal(heard, 2, "the two live moves, and no settled one");
  } finally {
    mock.timers.reset();
  }
});

test("subscribers hear the live and the settled moves, and nothing after off()", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const zoom = createPageScale(2);
    const a = [], b = [];
    const offA = zoom.subscribe(() => a.push([zoom.get(), zoom.rest()]));
    zoom.subscribe(() => b.push([zoom.get(), zoom.rest()]));
    zoom.set(3);
    mock.timers.tick(ZOOM_SETTLE_MS);
    assert.deepEqual(a, [[3, 2], [3, 3]]);
    offA();
    zoom.set(4);
    mock.timers.tick(ZOOM_SETTLE_MS);
    assert.deepEqual(a, [[3, 2], [3, 3]], "off() stops it");
    assert.deepEqual(b, [[3, 2], [3, 3], [4, 3], [4, 4]]);
  } finally {
    mock.timers.reset();
  }
});

test("dispose() cancels a pending rest, and set() works after it", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const zoom = createPageScale(1);
    zoom.set(2);
    zoom.dispose();
    mock.timers.tick(ZOOM_SETTLE_MS * 2);
    assert.equal(zoom.rest(), 1, "the pending rest is gone");
    assert.equal(zoom.get(), 2);
    zoom.set(3);
    mock.timers.tick(ZOOM_SETTLE_MS);
    assert.deepEqual([zoom.get(), zoom.rest()], [3, 3]);
  } finally {
    mock.timers.reset();
  }
});
