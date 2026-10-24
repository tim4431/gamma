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
// growing in, a toast rising, a body opening, a list's item gliding to
// its new place or rising into it (ListMotion).
const MOVING_ARRIVALS = new Set(["surface-in", "rise-in", "collapse-open", "list-move", "list-rise"]);

// The arrivals under way around `el`: those running on it and on what
// holds it (the popover growing in around a row). A box read before they
// end is not where `el` will stand: wait for their `finished` first. A
// fade, a transition or a highlight does not move it and is not counted.
export function arriving(el) {
  const out = [];
  for (let n = el; n?.getAnimations; n = n.parentElement) {
    for (const a of n.getAnimations()) {
      // a CSS animation by its keyframes' name, a script's by its id
      if (a.playState === "running" && MOVING_ARRIVALS.has(a.animationName ?? a.id)) out.push(a);
    }
  }
  return out;
}

// A list's change (ListMotion): which items glide to their new place, and
// by how much, and which new ones fade in. `items`, in document order, are
// {was, now, up, held}: the box before the change (null for a new item)
// and after it ({x, y, h}, in the window's pixels), the index of the
// nearest item holding it (-1 for none) and whether it must stay still (it
// holds a popup placed against the window). An item that moves carries
// what it holds, so each glides by its own move less its holder's: one
// easing over one time keeps them together. Only the boxes near the
// window glide (`top`, `bottom`), by no more than `far`; a change that
// would move more than `moving` items, or bring more than `entering` new
// ones, does that part at once. A new item fades in only where no new item
// holds it and it holds nothing that was there before (the body a row was
// just indented into), and it never travels: it stands where it will rest.
export function listGlides(items, { top = -Infinity, bottom = Infinity, far = Infinity, moving = 60, entering = 8 } = {}) {
  const near = (b) => b.y + (b.h || 0) >= top && b.y <= bottom;
  const holdsOld = new Set();
  items.forEach((it) => {
    if (it.was) for (let u = it.up; u >= 0 && !holdsOld.has(u); u = items[u].up) holdsOld.add(u);
  });
  const carried = [];
  const moves = [], enters = [];
  items.forEach((it, i) => {
    const held = it.up >= 0 ? carried[it.up] : { x: 0, y: 0 };
    carried[i] = held;
    if (!it.was) {
      if ((it.up < 0 || items[it.up].was) && !holdsOld.has(i) && near(it.now)) enters.push(i);
      return;
    }
    const dx = it.was.x - it.now.x, dy = it.was.y - it.now.y;
    const x = dx - held.x, y = dy - held.y;
    if (it.held || (Math.abs(x) < 0.5 && Math.abs(y) < 0.5)) return;
    if (!(near(it.was) || near(it.now)) || Math.abs(y) > far || Math.abs(x) > far) return;
    moves.push({ i, x, y });
    carried[i] = { x: dx, y: dy };
  });
  return { moves: moves.length > moving ? [] : moves, enters: enters.length > entering ? [] : enters };
}

// Whether two lists of keys are the same, in the same order.
export function sameKeys(a, b) {
  if (a === b) return true;
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}
