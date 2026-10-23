// The pure side of motion (docs/dev/ui-design.md, "Motion"): what the
// widgets (MotionWidgets.jsx), the leaving copies (leave.js) and whoever
// measures a surface need to know about it.

// Under the OS's reduced-motion setting nothing moves: a surface goes at
// once, a label changes in one step.
export const reducedMotion = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

// Where two texts part: the letters (code points) they share at the start
// and at the end, and what each has between them.
export function textSplit(from, to) {
  const a = Array.from(from), b = Array.from(to);
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  return {
    head: a.slice(0, head).join(""),
    from: a.slice(head, a.length - tail).join(""),
    to: b.slice(head, b.length - tail).join(""),
    tail: a.slice(a.length - tail).join(""),
  };
}

// The longest of a computed transition-duration list ("0.24s, 0.16s"), in ms.
export const longestMs = (list) => Math.max(0, ...String(list).split(",").map((t) => parseFloat(t) * (/ms/.test(t) ? 1 : 1000) || 0));

// The arrivals that move or size what they hold (motion.css): a surface
// growing in, a toast rising, a body opening.
const MOVING_ARRIVALS = new Set(["surface-in", "rise-in", "collapse-open"]);

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
