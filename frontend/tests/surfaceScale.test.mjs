// A screen distance in a surface's page units (src/ink/surfaceScale.js):
// the measure Duplicate's offset takes, from the selection menu and Ctrl+D.
import test from "node:test";
import assert from "node:assert/strict";
import { DUPLICATE_PX, duplicateOffset, measureSurface, pxToUnits, surfaceUnits } from "../src/ink/surfaceScale.js";

test("css px become page units by the surface's width on the screen over its width in units", () => {
  // a 612 pt page shown 816 css px wide (4/3 css px a point): 12 px is 9 pt
  assert.equal(pxToUnits(12, 816, 612), 9);
  assert.equal(pxToUnits(12, 612, 612), 12);
  assert.equal(pxToUnits(12, 1224, 612), 6);
  // nothing measured: a scale of one
  assert.equal(pxToUnits(12, 0, 612), 12);
  assert.equal(pxToUnits(12, 816, 0), 12);
  assert.equal(pxToUnits(12, undefined, undefined), 12);
});

test("a surface is measured by the layer that registered it, at its zoom when asked", () => {
  let css = 816;
  const stop = measureSurface(7, () => ({ css, width: 612 }));
  try {
    assert.equal(surfaceUnits(7, 12), 9);
    assert.deepEqual(duplicateOffset(7), { dx: 9, dy: 9 });
    // zoomed in to twice the size: the same 12 px is half as far on the page
    css = 1632;
    assert.deepEqual(duplicateOffset(7), { dx: 4.5, dy: 4.5 });
  } finally { stop(); }
  // gone with its layer: a scale of one
  assert.deepEqual(duplicateOffset(7), { dx: DUPLICATE_PX, dy: DUPLICATE_PX });
});

test("each surface answers for itself: a page and a sheet, and one nobody shows", () => {
  const stops = [measureSurface(1, () => ({ css: 1224, width: 612 })), measureSurface("sheet-a", () => ({ css: 595, width: 595 }))];
  try {
    assert.equal(surfaceUnits(1, 12), 6);
    assert.equal(surfaceUnits("sheet-a", 12), 12);
    assert.equal(surfaceUnits(2, 12), 12, "an unshown page");
    assert.equal(surfaceUnits("1", 12), 12, "a sheet id is not a page number");
  } finally { stops.forEach((stop) => stop()); }
});

test("a layer that stops measuring leaves the surface to the layer registered since", () => {
  const first = measureSurface(3, () => ({ css: 612, width: 612 }));
  const second = measureSurface(3, () => ({ css: 1224, width: 612 }));
  first();
  assert.equal(surfaceUnits(3, 12), 6, "the later layer still answers");
  second();
  assert.equal(surfaceUnits(3, 12), 12);
});
