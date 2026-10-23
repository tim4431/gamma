// The pure side of motion (shared/ui/motion.js, docs/dev/ui-design.md
// "Motion"): which arrivals are still under way around an element.
import assert from "node:assert/strict";
import { test } from "node:test";
import { arriving, reducedMotion } from "../src/shared/ui/motion.js";

test("arriving: the arrivals that move or size what they hold, on an element and what holds it", () => {
  const anim = (animationName, playState = "running") => ({ animationName, playState });
  const grow = anim("surface-in"), fade = anim("fade-in"), flash = anim("aiEditFadeA"), transition = { playState: "running" };
  const done = anim("surface-in", "finished");
  const popover = { getAnimations: () => [grow, fade, flash, transition], parentElement: null };
  const row = { getAnimations: () => [done, anim("rise-in")], parentElement: popover };
  assert.deepEqual(arriving(row).map((a) => a.animationName), ["rise-in", "surface-in"],
    "a toast rising and the popover growing in; not a fade, a highlight, a transition or what has ended");
  assert.deepEqual(arriving({ getAnimations: () => [], parentElement: null }), []);
});

test("reducedMotion: false where the setting can't be read", () => {
  assert.equal(reducedMotion(), false);
});
