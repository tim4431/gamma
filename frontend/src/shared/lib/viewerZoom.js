// How a viewer zooms: Ctrl/⌘ + wheel and the two-finger pinch, in one place.
// The PDF's pages (pdf/PdfViewer.jsx) and the notebook's sheets
// (notebook/NotebookViewer.jsx) both read their gestures from here, so a fix
// to how zooming feels reaches both and the two cannot drift apart.
//
// Committing a real zoom per input event is hopelessly janky — every commit
// re-lays-out every page. So the wheel's dispatch is coalesced to one per
// frame, and a pinch does not commit at all while the fingers are down: it
// only moves a CSS transform on the content layer (compositing, no layout;
// blurry until the fingers lift, like every native PDF app).
//
// The committed scale reaches the content as SCALE_VAR, written on the layer,
// and no page or sheet takes it as a prop, so a commit renders the viewer and
// not its pages. What sizes with the zoom says so in CSS: a box is
// scaledPx(its size at scale 1), and only those elements pick the variable
// up (`inherit`), once registerScaleVar below has made it non-inheriting.
//
// `el` is the scroller. Options:
//   layer()  the pinch preview's transform target: the scroller's first
//            in-flow child, so its top-left is content (0, 0) — which the
//            math below assumes — with `transform-origin: 0 0` in CSS.
//   clamp(s) the zoom limits (shared/model/zoom.js). Applied to the pinch
//            preview as well, so it never shows a zoom the commit would refuse.
//   onWheelZoom(next, at) at most once per frame: `next` is the scale the
//            wheel has reached, `at` the cursor in view coordinates, for the
//            viewer to hold that point of the content still.
//   onPinchZoom(next, gesture) on finger-lift. `next` is the clamped scale the
//            fingers asked for — equal to `gesture.scale` when they only
//            dragged, or when the pinch was already against a limit — and
//            `gesture` is { scale, sl, st, mx, my, vx, vy }: the scale and
//            scroll offsets it began at, the midpoint it began at, and the
//            midpoint it ended at (midpoints in view coordinates).
//   held()   a pen is down: a finger beside it is a palm, which neither
//            pinches nor pans, wherever in the viewer it lands.
//
// Returns { dispose, sync }.

// The committed zoom on a viewer's content layer (.pdfZoomLayer, .nbColumn).
export const SCALE_VAR = "--viewer-scale";
// A length that is `n` px at scale 1, at the layer's zoom.
export const scaledPx = (n) => `calc(var(${SCALE_VAR}) * ${n}px)`;

// Registers SCALE_VAR as a number that does not inherit, so a zoom restyles
// only what picks it up with `inherit` (app.css, notebook.css, the text
// layer's build). Left as a plain custom property it inherits, and each
// change restyles every element under the layer, every text-layer span and
// ink path, which costs more per frame than the page renders it saves.
// Registered only where a child's `inherit` follows its parent, checked once
// the way the app writes those styles: through the CSSOM, as React does.
// WebKit (26.6, checked) gets that case right, but not an element whose
// inline style is missing or only parsed from markup: its matched-properties
// cache hands that element the value it was first styled with. So every
// element that picks the variable up carries a style written by script. A
// browser without CSS.registerProperty, or one that fails the check, keeps
// the inheriting variable: slower per zoom frame, and the same picture.
// Returns whether it is registered.
let registered = null;
export function registerScaleVar(doc = globalThis.document) {
  if (registered !== null || !doc?.body) return !!registered;
  registered = false;
  const asNumber = (name) => doc.defaultView.CSS.registerProperty({ name, syntax: "<number>", inherits: false, initialValue: "1" });
  const check = `${SCALE_VAR}-check`;
  try { asNumber(check); } catch { return false; }
  const outer = doc.createElement("div"), inner = doc.createElement("div");
  outer.style.cssText = "position: absolute; visibility: hidden";
  outer.style.setProperty(check, "2");
  inner.style.setProperty(check, "inherit");
  inner.style.width = `calc(var(${check}) * 1px)`;
  inner.style.height = "1px";
  outer.append(inner);
  doc.body.append(outer);
  const first = inner.offsetWidth;
  outer.style.setProperty(check, "3");
  const follows = first === 2 && inner.offsetWidth === 3;
  outer.remove();
  if (follows) {
    try { asNumber(SCALE_VAR); registered = true; } catch { /* already registered: leave it as it is */ }
  }
  return registered;
}

// e^(-deltaY · RATE) per event, so a notch is a fixed ratio however many
// pixels the device reports.
const WHEEL_RATE = 0.0015;
// Firefox reports wheel deltas in lines rather than pixels (deltaMode 1).
const LINE_PX = 33;

export function installViewerZoom(el, { layer, clamp, onWheelZoom, onPinchZoom, held }) {
  registerScaleVar(el.ownerDocument);
  // The scale the viewer is heading to. It runs ahead of the committed scale
  // while a wheel train is in flight — the dispatch is coalesced to one per
  // frame and the render lands a frame later still — so steps compound off
  // this rather than off a scale that is one or two frames stale.
  let live = 1;
  let wheelRaf = 0;
  let wheelAt = null; // the cursor the pending dispatch should hold

  // --- Ctrl/⌘ + wheel (a trackpad pinch arrives as one) ---------------------
  // A native non-passive listener on purpose: React's root wheel listener is
  // passive, so preventDefault — needed to block the browser's own page zoom
  // — would not work from an onWheel prop.
  const onWheel = (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    const dy = e.deltaMode === 1 ? e.deltaY * LINE_PX : e.deltaY;
    const next = clamp(live * Math.exp(-dy * WHEEL_RATE));
    if (next === live) return; // pinned at a limit — don't leave a stale anchor behind
    live = next;
    const r = el.getBoundingClientRect();
    wheelAt = { x: e.clientX - r.left, y: e.clientY - r.top };
    if (!wheelRaf) {
      wheelRaf = requestAnimationFrame(() => {
        wheelRaf = 0;
        onWheelZoom(live, wheelAt);
      });
    }
  };

  // --- Two fingers: pinch to zoom, drag to pan -----------------------------
  // preventDefault on the two-finger move blocks native scrolling along with
  // the browser's own zoom (which the viewport meta turns off anyway — see
  // docs/dev/ipad.md). That makes panning the caller's job too: two fingers
  // held the same distance apart are a drag, and the commit gets the
  // midpoint's travel to move the view by.
  let start = null; // gesture-start snapshot: finger distance/midpoint, live scale, scroll
  let cur = null; // latest preview: effective ratio + midpoint
  const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  const mid = (t) => {
    const r = el.getBoundingClientRect();
    return { x: (t[0].clientX + t[1].clientX) / 2 - r.left, y: (t[0].clientY + t[1].clientY) / 2 - r.top };
  };
  const onTouchStart = (e) => {
    if (e.touches.length !== 2 || held()) return;
    const m = mid(e.touches);
    start = { scale: live, dist: dist(e.touches), mx: m.x, my: m.y, sl: el.scrollLeft, st: el.scrollTop };
    cur = null;
    const l = layer();
    if (l) l.style.willChange = "transform";
  };
  const onTouchMove = (e) => {
    if (held()) { if (e.cancelable) e.preventDefault(); return; }
    if (!start || e.touches.length !== 2) return;
    e.preventDefault();
    const m = mid(e.touches);
    const k = clamp(start.scale * (dist(e.touches) / start.dist)) / start.scale;
    cur = { k, vx: m.x, vy: m.y };
    // origin 0 0: keep the content that started under the midpoint glued to
    // the (moving) midpoint — visual = t + k·content − scroll, solve for t.
    const l = layer();
    if (l) {
      l.style.transform = `translate(${m.x + start.sl - k * (start.sl + start.mx)}px, `
        + `${m.y + start.st - k * (start.st + start.my)}px) scale(${k})`;
    }
  };
  const finish = () => {
    if (!start) return;
    const l = layer();
    if (l) { l.style.transform = ""; l.style.willChange = ""; }
    if (cur) {
      const next = clamp(start.scale * cur.k);
      live = next;
      onPinchZoom(next, {
        scale: start.scale, sl: start.sl, st: start.st, mx: start.mx, my: start.my, vx: cur.vx, vy: cur.vy,
      });
    }
    start = null; cur = null;
  };
  // A gesture ends as soon as it is no longer two fingers: the one left over
  // goes back to scrolling natively.
  const onTouchEnd = (e) => { if (e.touches.length < 2) finish(); };

  el.addEventListener("wheel", onWheel, { passive: false });
  el.addEventListener("touchstart", onTouchStart, { passive: true });
  el.addEventListener("touchmove", onTouchMove, { passive: false });
  el.addEventListener("touchend", onTouchEnd, { passive: true });
  el.addEventListener("touchcancel", onTouchEnd, { passive: true });

  return {
    dispose() {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", onTouchEnd);
      el.removeEventListener("touchcancel", onTouchEnd);
      cancelAnimationFrame(wheelRaf);
    },
    // The committed scale, which the viewer pushes in as it re-renders: a
    // zoom from anywhere else (the buttons, fit-width, a new document) is what
    // the next gesture has to compound on. Not while a dispatch is in flight,
    // though — events that arrived since are already compounded into `live`,
    // and overwriting it here would drop them (measurably: a 6-notch train
    // only zoomed about 3 notches' worth).
    sync(s) { if (!wheelRaf) live = clamp(s); },
  };
}
