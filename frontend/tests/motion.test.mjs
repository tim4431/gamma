// The pure side of motion (shared/ui/motion.js, docs/dev/ui-design.md
// "Motion"): which arrivals are still under way around an element.
import assert from "node:assert/strict";
import { test } from "node:test";
import { arriving, reducedMotion } from "../src/shared/ui/motion.js";

test("arriving: the finite animations running on an element and what holds it", () => {
  const anim = (playState, endTime) => ({ playState, effect: { getComputedTiming: () => ({ endTime }) } });
  const grow = anim("running", 240), spin = anim("running", Infinity), done = anim("finished", 240);
  const popover = { getAnimations: () => [grow, spin], parentElement: null };
  const row = { getAnimations: () => [done], parentElement: popover };
  assert.deepEqual(arriving(row), [grow], "the popover growing in; not a spinner, not what has ended");
  assert.deepEqual(arriving({ getAnimations: () => [], parentElement: null }), []);
});

test("reducedMotion: false where the setting can't be read", () => {
  assert.equal(reducedMotion(), false);
});
