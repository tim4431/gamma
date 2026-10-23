// The pure side of motion (docs/dev/ui-design.md, "Motion"): what the
// leaving copies (leave.js) and whoever measures a surface need to know
// about it.

// Under the OS's reduced-motion setting nothing moves: a surface goes at
// once.
export const reducedMotion = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

// The arrivals that move or size what they hold (motion.css): a surface
// growing in, a toast rising.
const MOVING_ARRIVALS = new Set(["surface-in", "rise-in"]);

// The arrivals under way around `el`: those running on it and on what
// holds it (the popover growing in around a row). A box read before they
// end is not where `el` will stand: wait for their `finished` first. A
// fade, a transition or a highlight does not move it and is not counted.
export function arriving(el) {
  const out = [];
  for (let n = el; n?.getAnimations; n = n.parentElement) {
    for (const a of n.getAnimations()) {
      if (a.playState === "running" && MOVING_ARRIVALS.has(a.animationName)) out.push(a);
    }
  }
  return out;
}
