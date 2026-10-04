// A step's scene: a short, looping demonstration drawn over the real UI —
// a ghost pointer that double-clicks a title, drags a window to where it
// would dock, draws a stroke, types into a field — so a card can say five
// words and show the rest. A scene is data on the step (`scene: [...]`);
// it never touches the app: nothing is clicked, typed or moved, it only
// draws in the overlay's scene layer (docs/dev/onboarding.md, "Scenes").
//
// Geometry is read from the anchors each time the scene plays, so it
// follows the layout. The scene steps aside while the user tries it (a
// press, or their pointer on a spot the scene uses), and comes back once
// they stop. Under
// reduced motion it shows its last frame, still. On a touch screen the
// pointer is a fingertip and a right-click is a long press.
import { anchorElement } from "./anchors.js";

// The guide's one timing scale, for scenes and for the demo pointer
// (guide.css's .guideCursor glide is MOTION.glide).
export const MOTION = {
  glide: 520,   // the pointer moving to its next target
  press: 90,    // a button going down, or up
  ripple: 520,  // the ring a press leaves
  dwell: 1600,  // the finished frame holds this long before the loop restarts
  rest: 450,    // the blank pause between loops
  fade: 160,    // shapes and the pointer fading in or out
  idle: 1500,   // how long the user must have left the scene alone before it comes back
  ease: "cubic-bezier(0.22, 0.61, 0.36, 1)",
  easeInOut: "cubic-bezier(0.45, 0, 0.25, 1)",
};

// The pointer both the scenes and the demos draw.
export const POINTER_PATH = "M2 2 L2 20 L7 15.5 L10.5 23 L14 21.5 L10.5 14 L17 14 Z";

// The primitives, each { <kind>: anchor, ...options } (`wait` takes ms):
//   point  {at?}                         glide there, with a soft hover halo
//   click  {at?, count?, button?}        press with a ripple; count 2 is a double-click, button "right" a right-click
//   drag   {at?, to: {zone, window}}     carry an outline of the anchor to where `window` would dock on
//                                        side `zone` ("left" | "right" | "bottom"), the dock's drop preview there
//   stroke {at?: [x0, y0, x1, y1]}       a handwriting squiggle inside that fraction of the anchor
//   type                                 a caret and a growing line of text inside a field
//   ghost  {size?}                       a dashed outline of what is about to appear under the anchor
//   wait   ms
// `at` is a point inside the anchor's box as fractions of it, [0.5, 0.5] by default.
export const SCENE_KINDS = ["point", "click", "drag", "stroke", "type", "ghost", "wait"];
const sceneKind = (primitive) => SCENE_KINDS.find((kind) => kind in primitive) || null;
export const DOCK_ZONES = ["left", "right", "bottom"];
export const DOCK_WINDOWS = ["chat", "notes"];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const SVG = "http://www.w3.org/2000/svg";

// An anchor's box, or a dock zone's ({zone, window}).
function boxOf(target, services) {
  const b = typeof target === "string" ? anchorElement(target)?.getBoundingClientRect()
    : services.dockZone?.(target.window, target.zone);
  return b?.width && b?.height
    ? { left: b.left, top: b.top, width: b.width, height: b.height, right: b.left + b.width, bottom: b.top + b.height } : null;
}
const pointIn = (box, at = [0.5, 0.5]) => ({ x: box.left + box.width * at[0], y: box.top + box.height * at[1] });
const translate = (p) => `translate(${p.x}px, ${p.y}px)`;

// A squiggle that reads as handwriting: a wave whose loops tighten and
// loosen across the box, as an SVG path in the box's own coordinates.
function squigglePath(w, h) {
  const mid = h / 2, amp = h * 0.36, loops = Math.max(2, Math.round(w / 46));
  let d = `M 0 ${mid}`;
  for (let i = 0; i < loops; i++) {
    const x0 = (w * i) / loops, x1 = (w * (i + 1)) / loops, dx = x1 - x0;
    const a = amp * (0.75 + 0.25 * Math.sin(i * 1.7));
    d += ` C ${x0 + dx * 0.3} ${mid - a}, ${x0 + dx * 0.7} ${mid - a}, ${x0 + dx * 0.55} ${mid}`;
    d += ` S ${x1 - dx * 0.1} ${mid + a * 0.8}, ${x1} ${mid}`;
  }
  return d;
}

// Plays `scene` in `layer` (the overlay's scene element) until the
// returned stop() is called. services.dockZone(window, side) gives a dock
// zone's box; touch draws a fingertip instead of a pointer.
export function playScene(layer, scene, { services = {}, reduced = false, touch = false } = {}) {
  let alive = true;
  let pass = 0;          // bumped to abandon the pass in flight
  let paused = false;
  let resume = 0;
  const running = new Set();
  const dur = (ms) => (reduced ? 0 : ms);

  // The pointer: one element for the whole scene, moved by transform.
  const pointer = document.createElement("div");
  pointer.className = `guidePointer guideScenePointer${touch ? " touch" : ""}`;
  pointer.innerHTML = touch ? '<span class="guideTouch"></span>'
    : `<svg width="22" height="26" viewBox="0 0 22 26" aria-hidden="true"><path d="${POINTER_PATH}" /></svg>`;
  const tip = pointer.firstElementChild;
  pointer.style.opacity = "0";
  layer.appendChild(pointer);
  let at = null;      // where the pointer is, or null while hidden
  let halo = null;
  let shapes = [];
  // The spots this pass played on: the user's pointer there means they are
  // trying it. A big anchor (the whole PDF pane) counts only where a
  // stroke went, so reading the card over it does not hold the scene back.
  let hot = [];
  const touches = (box) => { if (box) hot.push(box); return box; };

  const animate = (el, frames, opts) => {
    const anim = el.animate(frames, { duration: dur(opts.duration ?? MOTION.fade), easing: opts.easing ?? MOTION.ease, delay: dur(opts.delay ?? 0), fill: opts.fill ?? "none" });
    running.add(anim);
    anim.finished.catch(() => {}).finally(() => running.delete(anim));
    return anim;
  };
  // Ends the pass when the scene was stopped, paused or restarted meanwhile.
  const go = async (mine, promise) => {
    await promise;
    if (!alive || paused || mine !== pass) throw new Error("abandoned");
  };
  const shape = (className, box) => {
    const el = document.createElement("div");
    el.className = className;
    if (box) Object.assign(el.style, { left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px` });
    layer.insertBefore(el, pointer); // under the pointer, always
    shapes.push(el);
    return el;
  };
  const dropHalo = () => {
    if (!halo) return;
    const old = halo;
    halo = null;
    animate(old, [{ opacity: 1 }, { opacity: 0 }], {}).finished.then(() => old.remove(), () => old.remove());
  };
  // A right-click: a mouse beside the pointer with its right button lit.
  const showMouse = (on) => {
    let badge = pointer.querySelector(".guideMouse");
    if (!on) { badge?.remove(); return; }
    if (badge) return;
    badge = document.createElement("span");
    badge.className = "guideModifier guideMouse";
    badge.innerHTML = '<svg width="12" height="16" viewBox="0 0 12 16" aria-hidden="true"><rect x="1" y="1" width="10" height="14" rx="5" /><path d="M6 1v6M6 7h5V6a5 5 0 0 0-5-5z" /></svg>';
    pointer.appendChild(badge);
  };

  async function glide(mine, to, { duration, easing } = {}) {
    dropHalo();
    if (!at) {
      // It arrives from a little below and to the right of its first target.
      const from = { x: to.x + 34, y: to.y + 42 };
      pointer.style.transform = translate(from);
      pointer.style.opacity = "1";
      animate(pointer, [{ opacity: 0 }, { opacity: 1 }], {});
      at = from;
    }
    const distance = Math.hypot(to.x - at.x, to.y - at.y);
    const time = duration ?? clamp(260 + distance * 0.55, 380, MOTION.glide + 380);
    const from = at;
    pointer.style.transform = translate(to);
    at = to;
    await go(mine, animate(pointer, [{ transform: translate(from) }, { transform: translate(to) }], { duration: time, easing }).finished);
  }
  async function press(mine, point, { hold = false, long = false } = {}) {
    tip.style.transform = "scale(0.84)";
    await go(mine, animate(tip, [{ transform: "none" }, { transform: "scale(0.84)" }], { duration: MOTION.press }).finished);
    const ring = shape("guideSceneRipple", { left: point.x - 18, top: point.y - 18, width: 36, height: 36 });
    // A long press: the ring grows slowly while the finger stays down.
    const ripple = animate(ring, [{ opacity: 0.85, transform: "scale(0.35)" }, { opacity: 0, transform: "scale(1.5)" }],
      { duration: long ? MOTION.ripple * 1.6 : MOTION.ripple, fill: "forwards" });
    if (long) await go(mine, ripple.finished);
    if (hold) return;
    tip.style.transform = "";
    await go(mine, animate(tip, [{ transform: "scale(0.84)" }, { transform: "none" }], { duration: MOTION.press }).finished);
  }
  async function release(mine) {
    tip.style.transform = "";
    await go(mine, animate(tip, [{ transform: "scale(0.84)" }, { transform: "none" }], { duration: MOTION.press }).finished);
  }

  const play = {
    async point(mine, p) {
      const box = touches(boxOf(p.point, services));
      if (!box) return;
      const to = pointIn(box, p.at);
      await glide(mine, to);
      halo = shape("guideSceneHalo", { left: to.x - 16, top: to.y - 16, width: 32, height: 32 });
      animate(halo, [{ opacity: 0, transform: "scale(0.6)" }, { opacity: 1, transform: "none" }], { duration: 220 });
      await go(mine, sleep(dur(520)));
    },
    async click(mine, p) {
      const box = touches(boxOf(p.click, services));
      if (!box) return;
      const to = pointIn(box, p.at);
      await glide(mine, to);
      await go(mine, sleep(dur(160)));
      const right = p.button === "right";
      if (right && !touch) showMouse(true);
      for (let i = 0; i < (p.count || 1); i++) {
        if (i) await go(mine, sleep(dur(70)));
        await press(mine, to, { long: right && touch });
      }
      await go(mine, sleep(dur(420)));
      showMouse(false);
    },
    async drag(mine, p) {
      const fromBox = touches(boxOf(p.drag, services));
      const toBox = boxOf(p.to, services);
      if (!fromBox || !toBox) return;
      const start = pointIn(fromBox, p.at);
      const end = pointIn(toBox, [0.5, 0.42]);
      await glide(mine, start);
      await go(mine, sleep(dur(160)));
      await press(mine, start, { hold: true });
      // Where it lands: the dock's own drop preview, at the real geometry;
      // over it, an outline of the grabbed control riding with the pointer.
      const preview = shape("guideScenePreview", toBox);
      preview.style.opacity = "0";
      const carried = shape("guideSceneGhost guideSceneCarried", {
        left: fromBox.left, top: fromBox.top, width: Math.min(fromBox.width, 220), height: Math.min(fromBox.height, 64),
      });
      const time = clamp(500 + Math.hypot(end.x - start.x, end.y - start.y) * 0.7, 700, 1300);
      const shift = { x: end.x - start.x, y: end.y - start.y };
      carried.style.transform = translate(shift);
      animate(carried, [{ transform: "none", opacity: 0.4 }, { transform: translate(shift), opacity: 1 }], { duration: time, easing: MOTION.easeInOut });
      preview.style.opacity = "1";
      animate(preview, [{ opacity: 0 }, { opacity: 1 }], { duration: 220, delay: time * 0.35, fill: "backwards" });
      await glide(mine, end, { duration: time, easing: MOTION.easeInOut });
      await go(mine, sleep(dur(220)));
      await release(mine);
      const fadeCarried = animate(carried, [{ opacity: 1 }, { opacity: 0 }], {});
      fadeCarried.finished.then(() => carried.remove(), () => {});
      await go(mine, sleep(dur(500)));
    },
    async stroke(mine, p) {
      const box = boxOf(p.stroke, services);
      if (!box) return;
      const [x0, y0, x1, y1] = p.at || [0.25, 0.4, 0.6, 0.5];
      const area = { left: box.left + box.width * x0, top: box.top + box.height * y0, width: box.width * (x1 - x0), height: Math.max(14, box.height * (y1 - y0)) };
      touches({ ...area, right: area.left + area.width, bottom: area.top + area.height });
      const holder = shape("guideSceneInk", area);
      const svg = document.createElementNS(SVG, "svg");
      svg.setAttribute("width", area.width);
      svg.setAttribute("height", area.height);
      svg.setAttribute("viewBox", `0 0 ${area.width} ${area.height}`);
      const path = document.createElementNS(SVG, "path");
      path.setAttribute("d", squigglePath(area.width, area.height));
      svg.appendChild(path);
      holder.appendChild(svg);
      const length = path.getTotalLength();
      const along = (f) => { const q = path.getPointAtLength(length * f); return { x: area.left + q.x, y: area.top + q.y }; };
      await glide(mine, along(0));
      await press(mine, along(0), { hold: true });
      const time = clamp(length * 4.5, 900, 1800);
      path.style.strokeDasharray = `${length}px`;
      path.style.strokeDashoffset = "0px";
      animate(path, [{ strokeDashoffset: `${length}px` }, { strokeDashoffset: "0px" }], { duration: time, easing: "linear" });
      const frames = Array.from({ length: 33 }, (_, i) => ({ transform: translate(along(i / 32)) }));
      const last = along(1);
      pointer.style.transform = translate(last);
      at = last;
      await go(mine, animate(pointer, frames, { duration: time, easing: "linear" }).finished);
      await release(mine);
      await go(mine, sleep(dur(300)));
    },
    async type(mine, p) {
      const box = touches(boxOf(p.type, services));
      if (!box) return;
      // The pointer steps out of the way, as the demo's does while it types.
      if (at) {
        dropHalo();
        pointer.style.opacity = "0";
        await go(mine, animate(pointer, [{ opacity: 1 }, { opacity: 0 }], {}).finished);
        at = null;
      }
      // The field's own face, painted over its placeholder, so the line
      // types into what looks like an empty field (read, never changed).
      const field = anchorElement(p.type);
      const style = field && getComputedStyle(field);
      if (style && !/^(transparent|rgba\(0, 0, 0, 0\))$/.test(style.backgroundColor)) {
        const inset = parseFloat(style.borderTopWidth) || 0;
        const face = shape("guideSceneFace", { left: box.left + inset, top: box.top + inset, width: box.width - 2 * inset, height: box.height - 2 * inset });
        face.style.background = style.backgroundColor;
        face.style.borderRadius = `${Math.max(0, (parseFloat(style.borderTopLeftRadius) || 0) - inset)}px`;
        await go(mine, animate(face, [{ opacity: 0 }, { opacity: 1 }], {}).finished);
      }
      const height = clamp(box.height * 0.5, 10, 18);
      const x = box.left + Math.min(12, box.width * 0.06);
      const y = box.top + (box.height - height) / 2;
      const width = Math.min(box.width * 0.6, 180);
      const line = shape("guideSceneLine", { left: x, top: y + height / 2 - 3, width, height: 6 });
      const caret = shape("guideSceneCaret", { left: x, top: y, width: 2, height });
      const time = clamp(width * 7, 700, 1300);
      line.style.transform = "none";
      animate(line, [{ transform: "scaleX(0)" }, { transform: "none" }], { duration: time, easing: "linear" });
      caret.style.transform = `translateX(${width + 2}px)`;
      await go(mine, animate(caret, [{ transform: "none" }, { transform: `translateX(${width + 2}px)` }], { duration: time, easing: "linear" }).finished);
      await go(mine, sleep(dur(300)));
    },
    async ghost(mine, p) {
      const box = touches(boxOf(p.ghost, services));
      if (!box) return;
      const height = p.size || 40;
      const top = Math.min(box.bottom + 8, innerHeight - height - 8);
      if (top < box.top + box.height / 2) return; // no room under it
      const el = shape("guideSceneGhost", { left: box.left, top, width: box.width, height });
      await go(mine, animate(el, [{ opacity: 0, transform: "translateY(14px)" }, { opacity: 1, transform: "none" }], { duration: 420 }).finished);
      await go(mine, sleep(dur(300)));
    },
    async wait(mine, p) { await go(mine, sleep(dur(p.wait))); },
  };

  const clear = () => {
    running.forEach((anim) => anim.cancel());
    running.clear();
    shapes.forEach((el) => el.remove());
    shapes = [];
    halo = null;
    showMouse(false);
    tip.style.transform = "";
    pointer.style.opacity = "0";
    at = null;
  };

  async function loop() {
    const mine = ++pass;
    try {
      for (;;) {
        hot = [];
        for (const primitive of scene) {
          const kind = sceneKind(primitive);
          if (kind) await play[kind](mine, primitive);
        }
        if (reduced) return; // the last frame stays
        await go(mine, sleep(MOTION.dwell));
        dropHalo();
        const fading = [pointer, ...shapes].map((el) => animate(el, [{ opacity: getComputedStyle(el).opacity }, { opacity: 0 }], { fill: "forwards" }).finished);
        await go(mine, Promise.all(fading));
        clear();
        await go(mine, sleep(MOTION.rest));
      }
    } catch { /* stopped, paused or restarted */ }
  }

  // The user pressing, or their pointer on a spot the scene uses: they are
  // trying it, so the scene leaves; it comes back once they have been
  // elsewhere a while.
  const NEAR = 10;
  const inside = (e) => hot.some((r) => e.clientX >= r.left - NEAR && e.clientX <= r.right + NEAR
    && e.clientY >= r.top - NEAR && e.clientY <= r.bottom + NEAR);
  const onPointer = (e) => {
    if (!e.isTrusted || !alive) return;
    if (inside(e) || e.type === "pointerdown") {
      clearTimeout(resume);
      resume = 0;
      if (!paused) { paused = true; pass++; clear(); }
      return;
    }
    if (paused && !resume) resume = setTimeout(() => { resume = 0; paused = false; loop(); }, MOTION.idle);
  };
  window.addEventListener("pointermove", onPointer, { passive: true });
  window.addEventListener("pointerdown", onPointer, { passive: true });
  loop();

  return () => {
    alive = false;
    pass++;
    clearTimeout(resume);
    window.removeEventListener("pointermove", onPointer);
    window.removeEventListener("pointerdown", onPointer);
    clear();
    pointer.remove();
  };
}
