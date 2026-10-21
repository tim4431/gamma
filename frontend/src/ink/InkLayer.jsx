// Handwriting on the PDF (docs/dev/handwriting.md): InkLayer is one page's
// ink — the retained strokes as SVG paths in the page's scale-1 frame
// (the viewBox does the zoom), a low-latency canvas for the stroke (or
// lasso) being drawn, and the pointer handling that turns a pen (or, with
// a tool armed, any pointer) into samples, erasures, a lasso selection or
// a move of that selection. InkCard is the same strokes as a picture in
// the notes, with a replay of their writing that plays there and on the
// page (replay/playback.js). The tool strip is markup/MarkupToolbar.jsx, and
// a surface mounts its layer through markup/MarkupLayers.jsx. Strokes come
// from inkStore (drafts ahead of uploads, files behind block URLs); App owns
// the tool state, the selection, the stroke history and the commits.
import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { ContextMenu, MenuItem } from "../shared/ui/Menus";
import { getStroke } from "perfect-freehand";
import {
  ClipboardIcon, CopyIcon, DuplicateIcon, FileTextIcon, FlipHIcon, FlipVIcon, HighlightIcon, LineWidthIcon, MergeIcon, MoreIcon, NotebookPenIcon,
  PaletteIcon, PenIcon, PlayIcon, RectSelectIcon, RedoIcon, ResizeIcon, ScissorsIcon, StopIcon, TrashIcon, BrushIcon,
} from "../shared/ui/Icons";
import {
  MAX_STROKE_SIZE, boundsOf, byBand, cachedStrokePath, dashDots, dashPattern, dashRuns, encodeStroke, eraserKeep, hitStrokes,
  inkBounds, inkFeatures, inkUntil, lerpSample, nearestInkStroke, opacityBand, outlineOptions, outlinePressure, runOutline,
  sizesFor, strokeBand, strokeId, strokeMaxWidth, strokePath, strokePlaceholder, strokeWalls, strokesInLasso, svgPathFromPoints,
  transformPoint, unionBox,
} from "./ink";
import { brushOpacities, brushWidths, finishStroke, strokeChannels, widthLaw } from "./finishStroke.js";
import { GRAIN_PX, GRAIN_UNITS, grainImage, grainUri } from "./grain.js";
import { InkLineChoices, InkStyleMenu, InkStylePicker, brushValue } from "./InkStylePicker.jsx";
import { recognise, refitShape, shapeSamples } from "./shapes.js";
import { makeStabilizer } from "./stabilizer.js";
import { makeChordDetector } from "./fingerChords.js";
import { INK_COLORS, colorName, pickerColors, swatchCss } from "../shared/model/palette.js";
import { usePalette } from "../shared/ui/paletteContext.js";
import * as inkStore from "./inkStore";
import * as playback from "../replay/playback.js";
import { compressedClock } from "../replay/clock.js";
import { frameOf, groupInScope, useGroupFrame } from "../replay/useFrame.js";
import { appendInkSample, inkSample, penTilt, predictedInkSamples } from "./inkInput.js";
import * as inkLive from "./inkLive.js";
import { canvasSize } from "../shared/lib/canvasSize.js";
import { nativeHost, onHostMessage, postToHost } from "../shared/lib/host.js";
import { t, T } from "../shared/i18n/i18n.js";

// Words for colours, so tooltips read "Pink highlighter", not "#f9a8d4":
// every colour, a custom one included, is named after the nearest of the
// palette module's reference colours.
export const inkColorName = (hex) => t(colorName(hex) || T("Custom color"));
const inkKindName = (kind) => (kind === "highlighter" ? t("Highlighter") : t("Pen"));

// Re-render when any draft or file changes.
export function useInkVersion() {
  const [v, setV] = useState(inkStore.currentVersion());
  useEffect(() => inkStore.subscribe(setV), []);
  return v;
}

// Eraser radius on screen (css px) per S/M/L index.
export const ERASER_SIZES = [5, 9, 16];
// What a press on never starts ink, even from a stylus that writes right
// away: the menus (portalled), the lasso's handles, and a text box's width
// handle, its move band and its open editor (markup/TextBoxLayer.jsx).
const NOT_INK = ".inkEditMenu, .inkTransformHandle, .textBoxWidth, .textBoxBand, .textBox.editing";
// What takes a click above a stroke of a read-only page (whose tap shows
// the stroke's note): the page's own clickable marks. The strokes are drawn
// above them all, text boxes included (app.css), but take no pointer.
const ABOVE_PASSIVE_INK = "[data-hl-id], .pdfNoteAnchor, .pdfLinkBox, .textBox";
// Under a native host that captures the Pencil (shared/lib/host.js), a pen
// stroke is claimed at its down: the host takes the touch and draws the live
// line itself, then hands the finished samples back, and they commit here as
// the browser's own stroke would. The claims by pointer id: {commit, taken}.
// A taken claim always ends with a `stroke` message: the host sends one
// with no samples when the system cancels the touch.
const hostClaims = new Map();
if (nativeHost()?.ink) {
  onHostMessage("stroke", async ({ pointerId, samples }) => {
    const claim = hostClaims.get(pointerId);
    hostClaims.delete(pointerId);
    // No samples: the system cancelled the touch, and the claim just ends.
    const committed = !!claim?.commit(samples || []);
    // The reply drops the host's live line: wait for React's commit and a
    // paint, so the stroke never blinks out between the two.
    await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
    return { committed };
  });
}
// Whether a finger scrolling under an armed tool is still worth explaining
// (onFingerScroll): once a session, and never once a pen has drawn.
let fingerHint = true;
// Draw and hold (shapes.js): the tip still for HOLD_MS fires the recogniser,
// the timer restarting whenever it moves more than HOLD_MOVE css px, and only
// once the stroke lasted HOLD_MIN_MS; held on, a move of REFIT_PX refits the
// shape, and one of more than CANCEL_PX within CANCEL_MS of the snap cancels
// it, so a stroke that merely paused goes on freehand.
const HOLD_MS = 600, HOLD_MOVE = 3, HOLD_MIN_MS = 250, REFIT_PX = 6, CANCEL_PX = 12, CANCEL_MS = 150;
// The same timer, armed at the down when the clipboard holds strokes, opens
// "Paste here" if the tip has not travelled PASTE_MOVE css px by then.
const PASTE_MOVE = 2;
const SNAP_BLUE = "rgb(80, 140, 255)"; // .inkSelRect's stroke
// Two- and three-finger undo chords (fingerChords.js): one detector for the
// document — several page layers must never count a contact twice — fed by
// document-level listeners while at least one layer with an onUndoGesture is
// mounted, so chords exist exactly where handwriting can be drawn. A touch on
// a layer's surface names the host whose mode and handler the chord takes.
const chordHosts = new Map(); // a layer's wrap element → its live ref
let chordWiring = null;
function wireChords() {
  if (chordWiring) return;
  let host = null;
  const detector = makeChordDetector({ mode: "off", onChord: (kind) => host?.onUndoGesture?.(kind) });
  const hostAt = (target) => {
    for (const [el, ref] of chordHosts) if (el.contains(target)) return ref.current;
    return null;
  };
  // The first contact on a layer names the host; one landing off every
  // layer (the gutter, the strip) changes nothing, so it neither flips the
  // mode mid-chord nor forgets the first chord of a double tap. A layer
  // that is writing with a pen the detector cannot see (the native host
  // owns the Pencil, so WebKit cancelled its pointer) makes the touches a
  // palm.
  const down = (e) => {
    if (e.pointerType === "touch") {
      const at = hostAt(e.target);
      if (at) { host = at; detector.setMode(at.fingerUndo || "off"); }
    }
    detector.down(e);
    if (e.pointerType === "touch" && host?.penBusy?.()) detector.palm();
  };
  const move = (e) => detector.move(e);
  const up = (e) => detector.up(e);
  const cancel = (e) => detector.cancel(e);
  const reset = () => detector.reset();
  document.addEventListener("pointerdown", down, true);
  document.addEventListener("pointermove", move, true);
  document.addEventListener("pointerup", up, true);
  document.addEventListener("pointercancel", cancel, true);
  window.addEventListener("blur", reset);
  chordWiring = () => {
    document.removeEventListener("pointerdown", down, true);
    document.removeEventListener("pointermove", move, true);
    document.removeEventListener("pointerup", up, true);
    document.removeEventListener("pointercancel", cancel, true);
    window.removeEventListener("blur", reset);
    chordWiring = null;
  };
}
// Drawings already checked for what this build cannot show (inkFeatures);
// a drawing is told of once, through onInkFeatures.
const featured = new WeakSet();

// The click a handled pointer-up would deliver to whatever lies under it
// (a highlight rect, a link box) is not a click on that thing.
export function swallowClick() {
  const swallow = (ce) => { ce.stopPropagation(); ce.preventDefault(); };
  document.addEventListener("click", swallow, { capture: true, once: true });
  setTimeout(() => document.removeEventListener("click", swallow, { capture: true }), 0);
}

// --- the pencil's grain --------------------------------------------------------
// A pencil stroke is filled with its colour's grain (grain.js): one
// <pattern> per pencil colour drawn on the surface, in the SVG's own <defs>
// (ids are the document's, so every layer and card names its own), its
// tile GRAIN_UNITS page units from the page's corner, so the grain zooms
// with the paper and meets the live canvas's. Strokes reads the fill
// through GrainContext; without it a pencil is filled plain.
const GrainContext = React.createContext(null);
const colorKey = (color) => String(color).toLowerCase().replace(/[^a-z0-9]/g, "");
function useGrain(inks) {
  const prefix = "inkGrain-" + useId().replace(/[^A-Za-z0-9_-]/g, "");
  const colors = new Set();
  for (const ink of inks) for (const s of ink?.strokes || []) if (s.brush === "pencil") colors.add(String(s.color || INK_COLORS[0]).toLowerCase());
  const key = [...colors].sort().join(" ");
  const fill = React.useMemo(() => {
    const known = new Set(key ? key.split(" ") : []);
    return (color) => {
      const c = String(color || INK_COLORS[0]).toLowerCase();
      return known.has(c) ? `url(#${prefix}-${colorKey(c)})` : null;
    };
  }, [prefix, key]);
  const defs = key ? (
    <defs>
      {key.split(" ").map((c) => (
        <pattern key={c} id={`${prefix}-${colorKey(c)}`} patternUnits="userSpaceOnUse" width={GRAIN_UNITS} height={GRAIN_UNITS}>
          <image href={grainUri(c)} width={GRAIN_UNITS} height={GRAIN_UNITS} preserveAspectRatio="none" />
        </pattern>
      ))}
    </defs>
  ) : null;
  return { fill, defs };
}

// A drawing's strokes as SVG paths (ink.js strokePath): a pen's filled
// outline (its dash runs or dots when dashed or dotted; a pencil's a path
// per shade, filled with the grain), a constant-width dashed pen's and a
// highlighter's stroked polyline, the highlighter multiplied onto the page,
// an unknown tool's polyline. Band 0 (under) draws before band 1 (ink);
// `band` restricts the drawing to one band, for a layer that interleaves
// several groups' bands.
export function Strokes({ ink, hide, band, lazy = false }) {
  const grainFill = React.useContext(GrainContext);
  const strokes = band == null ? byBand(ink.strokes) : ink.strokes.filter((s) => strokeBand(s) === band);
  return strokes.map((s) => {
    if (hide?.has(s.id)) return null;
    // `lazy` (a dense group, "dense pages" below): a stroke whose outline is
    // still to be built stands in as its placeholder polyline meanwhile
    const p = lazy ? cachedStrokePath(s) || strokePlaceholder(s) : strokePath(s);
    const color = s.color || INK_COLORS[0], opacity = s.opacity ?? 1;
    const paint = (p.grain && grainFill?.(color)) || color;
    if (p.bands) {
      return (
        <React.Fragment key={s.id}>
          {p.bands.map((b) => <path key={b.alpha} d={b.d} fill={paint} fillOpacity={b.alpha * opacity} />)}
        </React.Fragment>
      );
    }
    return p.stroke ? (
      <path key={s.id} d={p.d} fill="none" stroke={paint} strokeWidth={p.width} strokeOpacity={opacity}
        strokeLinecap="round" strokeLinejoin="round" strokeDasharray={p.dash ?? undefined}
        style={p.multiply ? { mixBlendMode: "multiply" } : undefined} />
    ) : (
      <path key={s.id} d={p.d} fill={paint} fillOpacity={opacity} />
    );
  });
}

// A group as it stands in a replay (replay/useFrame.js frameOf): the
// strokes begun so far, the one in progress cut short, and the ones not
// yet reached faded (spotlight), left out (reveal) or drawn as they are
// (static). Without a frame, the drawing as it is.
export function ReplayStrokes({ ink, frame, hide, band, lazy = false }) {
  if (!frame || frame.mode === "static") return <Strokes ink={ink} hide={hide} band={band} lazy={lazy} />;
  const until = inkUntil(ink, frame.at, frame.times);
  const pending = new Set(frame.mode === "spotlight" ? until.pending : []);
  return (
    <>
      {pending.size ? (
        <g className="inkPending"><Strokes ink={{ ...ink, strokes: ink.strokes.filter((s) => pending.has(s.id)) }} hide={hide} band={band} /></g>
      ) : null}
      <Strokes ink={until.ink} hide={hide} band={band} />
    </>
  );
}

// A tap on a stroke under a playing recording plays the moment it was
// written (replay/playback.js); false when the stroke carries no time or
// the recording does not cover it.
function seekToStroke(groups, hit) {
  const stroke = groups.find((g) => g.id === hit.id)?.ink.strokes.find((s) => s.id === hit.ids?.[0]);
  const wall = stroke ? strokeWalls(stroke)?.[0] : null;
  return wall != null && playback.seekBefore(wall);
}

// --- the live canvas ----------------------------------------------------------
// One painter for a stroke in progress, the page's own (InkLayer's paint)
// and a peer's wet stroke (paintWet), from the brush table on the samples
// so far (finishStroke.js brushWidths, brushOpacities: a Brush's start
// taper only until `done`, at its full length, so a short mark takes its
// shorter tapers at the lift), so the line under the pen, the line a peer
// sees while it is written and the committed stroke match. `use` is the
// style, {tool, brush, dash, size, color, opacity, pen} — `pen` false is a
// stroke without pressure, whose Brush follows the speed law. A dashed
// constant-width line is stroked with setLineDash, a dashed pressure line
// cut into its runs (dotted: a circle per dot); a pencil is stamped from
// round dabs of its grain (paintPencil). `law` null draws an even line
// at the nominal size (a style with no width law, and a held shape's
// preview: a shape commits at constant width whatever its brush, with no
// tapers and no shading); `color` the fill (the held shape's snap flash is
// blue); `flash` that flash's frames.
export function paintLiveStroke(ctx, samples, use, { law, color = use.color, flash = false, done = false } = {}) {
  if (!samples.length) return;
  const dash = dashPattern(use);
  if (use.tool === "highlighter") {
    ctx.globalAlpha = flash ? 0.95 : use.opacity ?? 1;
    ctx.globalCompositeOperation = flash ? "source-over" : "multiply";
    ctx.strokeStyle = color;
    ctx.lineWidth = use.size;
    ctx.lineCap = ctx.lineJoin = "round";
    ctx.setLineDash(dash || []);
    ctx.lineDashOffset = 0;
    ctx.beginPath();
    samples.forEach((s, i) => (i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y)));
    if (samples.length === 1) ctx.lineTo(samples[0].x + 0.01, samples[0].y);
    ctx.stroke();
    ctx.setLineDash([]);
    return;
  }
  const ws = law === null ? null : brushWidths(samples, use, { live: !done });
  const os = law === null ? null : brushOpacities(samples, use);
  const pts = ws || os ? samples.map((s, i) => ({ ...s, ...(ws ? { w: ws[i] } : null), ...(os ? { o: os[i] } : null) })) : samples;
  const alpha = flash ? 1 : use.opacity ?? 1;
  ctx.globalCompositeOperation = "source-over";
  if (use.brush === "pencil" && !flash) { paintPencil(ctx, pts, use, color, alpha, dash); return; }
  ctx.globalAlpha = alpha;
  if (dash && !ws) {
    // a constant-width line: its centreline stroked with the pattern, as the SVG draws it
    ctx.strokeStyle = color;
    ctx.lineWidth = use.size;
    ctx.lineCap = ctx.lineJoin = "round";
    ctx.setLineDash(dash);
    ctx.lineDashOffset = 0;
    ctx.beginPath();
    pts.forEach((s, i) => (i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y)));
    if (pts.length === 1) ctx.lineTo(pts[0].x + 0.01, pts[0].y);
    ctx.stroke();
    ctx.setLineDash([]);
    return;
  }
  ctx.fillStyle = color;
  const opts = outlineOptions({ size: use.size });
  const outline = (run) => svgPathFromPoints(getStroke(run.map((s) => [s.x, s.y, outlinePressure(s)]), opts));
  if (dash && use.dash === "dotted") {
    const path = new Path2D();
    for (const q of dashDots(pts, use)) {
      const r = use.size * (q.w ?? 1) / 2;
      path.moveTo(q.x + r, q.y);
      path.arc(q.x, q.y, r, 0, 2 * Math.PI);
    }
    ctx.fill(path);
    return;
  }
  // every dash outlined on its own as the SVG's are, the first while it is
  // the only one too (ink.js runOutline: a run shorter than 3 pt, or of two
  // samples, would otherwise not draw its width)
  ctx.fill(new Path2D(dash ? dashRuns(pts, use).map((run) => runOutline(run, opts)).join(" ") : outline(pts)));
}
// The canvas pattern of a colour's grain, per context: GRAIN_PX pixels over
// GRAIN_UNITS page units from the page's corner, as the SVG's pattern.
const grainPatterns = new WeakMap();
function grainPattern(ctx, img) {
  let mine = grainPatterns.get(ctx);
  if (!mine) grainPatterns.set(ctx, (mine = new Map()));
  let pattern = mine.get(img);
  if (pattern === undefined) {
    pattern = ctx.createPattern(img, "repeat");
    const k = GRAIN_UNITS / GRAIN_PX;
    pattern?.setTransform?.(new DOMMatrix([k, 0, 0, k, 0, 0]));
    mine.set(img, pattern);
  }
  return pattern;
}
// A pencil in progress: a round dab every quarter width along the samples
// (never closer than a device pixel), its diameter the drawn width there,
// size × w — the band the saved outline covers, so the line under the pen
// is the committed line; the lean is in w already (the width law), as it is
// in the saved stroke — filled with the grain, one fill per opacity band
// (the shades the SVG draws, opacity × band), so overlapping dabs never
// darken one another.
function paintPencil(ctx, samples, use, color, alpha, dash) {
  const size = use.size || 1, img = grainImage(color);
  const m = ctx.getTransform?.(), pixel = m ? 1 / Math.max(1e-6, Math.hypot(m.a, m.b)) : 0.05;
  ctx.fillStyle = (img && grainPattern(ctx, img)) || color;
  const bands = new Map();
  const dab = (s) => {
    const band = opacityBand(s.o);
    let path = bands.get(band);
    if (!path) bands.set(band, (path = new Path2D()));
    const r = size * (s.w ?? 1) / 2;
    path.moveTo(s.x + r, s.y);
    path.arc(s.x, s.y, r, 0, 2 * Math.PI);
  };
  if (dash && use.dash === "dotted") for (const q of dashDots(samples, use)) dab(q);
  else {
    for (const run of dash ? dashRuns(samples, use) : [samples]) {
      dab(run[0]);
      for (let i = 1; i < run.length; i++) {
        const a = run[i - 1], b = run[i];
        const step = Math.max(pixel, size * Math.min(a.w ?? 1, b.w ?? 1) / 4);
        const n = Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / step);
        for (let k = 1; k <= n; k++) dab(k === n ? b : lerpSample(a, b, k / n));
      }
    }
  }
  for (const [band, path] of bands) {
    ctx.globalAlpha = alpha * band;
    ctx.fill(path);
  }
}
// A surface's wet strokes, the peers' strokes in progress (inkLive.js):
// painted on a canvas of their own under this layer's stroke in progress,
// repainted on every chunk, each through paintLiveStroke with its style's
// width law baked on the fly (the start taper only, like the writer's own
// live line). One a loaded file of the surface holds is gone (landWet), so
// the committed stroke takes its place with no visible change. The
// canvas's data-wet counts the strokes drawn and data-wet-ids names them.
function useWetStrokes(surface, wrapRef, wetRef, live) {
  const paintRef = useRef(null);
  useEffect(() => {
    let raf = 0;
    const paintWet = () => {
      raf = 0;
      const canvas = wetRef.current, el = wrapRef.current, L = live.current;
      if (!canvas || !el) return;
      let strokes = inkLive.wetOn(surface);
      if (strokes.length) {
        const wet = new Set(strokes.map((s) => s.id)), landed = [];
        for (const g of L.groups || []) for (const s of g.ink.strokes) if (wet.has(s.id)) landed.push(s.id);
        if (landed.length && inkLive.landWet(surface, landed)) strokes = inkLive.wetOn(surface);
      }
      const rect = el.getBoundingClientRect();
      if (!strokes.length || !L.width || !rect.width) {
        if (canvas.width || canvas.style.display === "block") { canvas.width = 0; canvas.height = 0; canvas.style.display = "none"; }
        canvas.dataset.wet = "0";
        canvas.dataset.wetIds = "";
        return;
      }
      const size = canvasSize(rect.width, rect.height, window.devicePixelRatio || 1);
      if (canvas.width !== size.width || canvas.height !== size.height) { canvas.width = size.width; canvas.height = size.height; }
      canvas.style.display = "block";
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const k = rect.width / L.width;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(canvas.width / rect.width * k, 0, 0, canvas.height / rect.height * k, 0, 0);
      for (const s of strokes) {
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = "source-over";
        ctx.setLineDash([]);
        paintLiveStroke(ctx, s.samples, s.style, { law: widthLaw(s.style), done: s.done });
      }
      canvas.dataset.wet = String(strokes.length);
      canvas.dataset.wetIds = strokes.map((s) => s.id).join(" ");
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(paintWet); };
    paintRef.current = schedule;
    const off = inkLive.subscribeWet(surface, schedule);
    schedule();
    return () => { off(); cancelAnimationFrame(raf); paintRef.current = null; };
  }, [surface, wrapRef, wetRef, live]);
  // A file landing (the groups change) may hold a wet stroke's id now.
  useEffect(() => { if (inkLive.wetOn(surface).length) paintRef.current?.(); });
}
// Whether peers are writing on a surface right now: a read-only surface
// without ink mounts its layer for their wet strokes (markup/MarkupLayers.jsx).
export function useHasWet(surface) {
  const [has, setHas] = useState(() => inkLive.wetOn(surface).length > 0);
  useEffect(() => {
    const check = () => setHas(inkLive.wetOn(surface).length > 0);
    check();
    return inkLive.subscribeWet(surface, check);
  }, [surface]);
  return has;
}

// --- dense pages ----------------------------------------------------------------
// What keeps a surface holding hundreds or thousands of strokes fluid
// (docs/dev/handwriting.md "Client", culling and the raster). None of it
// changes what is drawn, only when and how: a tap's selection, the lasso
// and the eraser keep reading the samples, and a picture takes no presses
// (the page under a group's box stays the page's).
export const IDLE_STROKES = 200;      // a group past this mounts with placeholders
const IDLE_SLICE = 50;                // outlines built per idle slice
export const RASTER_STROKES = 1500;   // a surface past this shows its finished groups as pictures
const RASTER_QUIET_MS = 1000;         // a group unchanged this long is finished
const RASTER_PIXELS = 16 * 1024 * 1024;   // device pixels a layer's pictures hold at most (64 MB decoded)
const idleLater = typeof requestIdleCallback === "function"
  ? (fn) => requestIdleCallback(fn, { timeout: 500 }) : (fn) => setTimeout(fn, 0);
const idleCancel = typeof cancelIdleCallback === "function" ? (h) => cancelIdleCallback(h) : (h) => clearTimeout(h);
// Whether a group draws its strokes not built yet as placeholders: past
// IDLE_STROKES strokes, and with more than a slice of them waiting (a new
// stroke or two is built at once, so a stroke never blinks thin on lift).
function lazyGroup(ink) {
  if (ink.strokes.length <= IDLE_STROKES) return false;
  let waiting = 0;
  for (const s of ink.strokes) if (!cachedStrokePath(s) && ++waiting > IDLE_SLICE) return true;
  return false;
}
// The outlines a lazy group still waits for, built in idle slices of
// IDLE_SLICE strokes, each slice swapped in as it lands (a re-render): the
// groups on screen only (`shown`).
function useIdleOutlines(groups, shown) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const todo = [];
    for (const g of groups) {
      if (g.ink.strokes.length <= IDLE_STROKES || (shown && !shown.has(g.id))) continue;
      for (const s of g.ink.strokes) if (!cachedStrokePath(s)) todo.push(s);
    }
    if (!todo.length) return undefined;
    let live = true, handle = 0;
    const step = () => {
      for (let n = 0; todo.length && n < IDLE_SLICE; n++) strokePath(todo.shift());
      if (!live) return;
      if (todo.length) handle = idleLater(step);
      setTick((t) => t + 1);
    };
    handle = idleLater(step);
    return () => { live = false; idleCancel(handle); };
  });
}
// Culling: on a surface holding more than IDLE_STROKES strokes, a group
// whose box lies outside the part of the page in its scroller, plus half
// that scroller on every side, renders nothing. → the ids to draw, or null
// (all, a lighter surface). Measured before the paint of every render and
// on every scroll frame; an IntersectionObserver on the page tells when the
// page itself is that far off (nothing to draw).
function useVisibleGroups(wrapRef, groups, width, height, enabled) {
  const [visible, setVisible] = useState(null);
  const boxes = useRef([]);
  boxes.current = enabled ? groups.map((g) => [g.id, inkBounds(g.ink)]) : [];
  const measureRef = useRef(null);
  useLayoutEffect(() => {
    if (!enabled) { measureRef.current = null; setVisible(null); return undefined; }
    const el = wrapRef.current;
    if (!el || !width || !height) return undefined;
    const view = viewportOf(el);
    let near = true, raf = 0;
    const measure = () => {
      raf = 0;
      const ids = new Set(), r = el.getBoundingClientRect();
      if (near && r.width && r.height) {
        const v = view ? view.getBoundingClientRect() : { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
        const ky = r.height / height, kx = r.width / width, my = (v.bottom - v.top) / 2, mx = (v.right - v.left) / 2;
        const y0 = (v.top - my - r.top) / ky, y1 = (v.bottom + my - r.top) / ky;
        const x0 = (v.left - mx - r.left) / kx, x1 = (v.right + mx - r.left) / kx;
        for (const [id, b] of boxes.current) if (b && b[3] >= y0 && b[1] <= y1 && b[2] >= x0 && b[0] <= x1) ids.add(id);
      }
      setVisible((prev) => (prev && prev.size === ids.size && [...ids].every((id) => prev.has(id)) ? prev : ids));
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(measure); };
    const io = typeof IntersectionObserver === "function"
      ? new IntersectionObserver((entries) => { near = entries.at(-1).isIntersecting; schedule(); }, { root: view, rootMargin: "50%" })
      : null;
    io?.observe(el);
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(schedule) : null;   // a zoom
    ro?.observe(el);
    window.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    measureRef.current = measure;
    measure();
    return () => {
      io?.disconnect();
      ro?.disconnect();
      window.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      cancelAnimationFrame(raf);
      measureRef.current = null;
    };
  }, [enabled, wrapRef, width, height]);
  // the groups changed (a stroke, a file): measured again before the paint
  useLayoutEffect(() => { measureRef.current?.(); });
  return enabled ? visible || NO_GROUPS : null;
}
const NO_GROUPS = new Set();
// The raster: on a surface holding more than RASTER_STROKES strokes, every
// finished group's <g> (one per band) is drawn once as a picture of itself
// — its own markup with the layer's <defs> as an SVG image, drawn to a
// canvas at the current zoom in device pixels — and shown as one <image>
// until the group changes. A zoom draws it again from the markup it kept,
// the old picture standing meanwhile. Not for a group in `busy` (the one
// this layer's last stroke went into, one with a selection, one in a
// replay), one not drawn (culled), one too large for a canvas (canvasSize's
// caps), or one past what the layer's pictures may hold together
// (RASTER_PIXELS; the pictures of groups scrolled away go first to make
// room): those keep their paths. → raster(key "id:band", ink) → {url, box:
// [x, y, w, h]} | null.
function useGroupRasters(svgRef, wrapRef, groups, width, enabled, busy, shown, lazy) {
  const store = useRef(new Map());   // key → {ink, markup, scale, box, px (device pixels), url, working, failed}
  const lazyRef = useRef(lazy);
  lazyRef.current = lazy;            // the groups this render drew with placeholders
  const [, setTick] = useState(0);
  const drop = (key) => {
    const r = store.current.get(key);
    if (!r) return;
    r.dead = true;
    if (r.url) URL.revokeObjectURL(r.url);
    store.current.delete(key);
  };
  useEffect(() => () => { for (const key of [...store.current.keys()]) drop(key); }, []);
  // a zoom changes the page's size: render, so the pictures are made again at it
  useEffect(() => {
    const el = wrapRef.current;
    if (!enabled || !el || typeof ResizeObserver !== "function") return undefined;
    const ro = new ResizeObserver(() => setTick((n) => n + 1));
    ro.observe(el);
    return () => ro.disconnect();
  }, [enabled, wrapRef]);
  useEffect(() => {
    // keep: the groups whose picture may stand; make: those it may be made of now (drawn)
    const map = store.current, keep = new Map(), make = new Map();
    if (enabled) {
      for (const g of groups) {
        if (busy.has(g.id)) continue;
        for (const band of [0, 1]) {
          if (!g.ink.strokes.some((s) => strokeBand(s) === band)) continue;
          keep.set(`${g.id}:${band}`, g);
          if (!shown || shown.has(g.id)) make.set(`${g.id}:${band}`, g);
        }
      }
    }
    // the picture of a group gone, busy or changed goes (its paths show again)
    for (const [key, r] of [...map]) if (!keep.has(key) || keep.get(key).ink !== r.ink) drop(key);
    if (!make.size) return undefined;
    const timer = setTimeout(() => {
      const el = wrapRef.current, svg = svgRef.current;
      if (!el || !svg || !width) return;
      const scale = el.getBoundingClientRect().width / width * (window.devicePixelRatio || 1);
      // the pixels the layer's pictures hold, one being made at the size it is made at
      let used = 0;
      for (const r of map.values()) if (r.url || r.working) used += r.px || 0;
      for (const [key, g] of make) {
        const r = map.get(key);
        // drawn (or found too large) at this zoom already, or on its way
        if (r && (r.working || Math.abs(r.scale - scale) <= scale * 0.01)) continue;
        const b = inkBounds(g.ink);
        if (!b) continue;
        const box = [b[0] - 1, b[1] - 1, b[2] - b[0] + 2, b[3] - b[1] + 2];
        const w = Math.ceil(box[2] * scale), h = Math.ceil(box[3] * scale), cap = canvasSize(w, h, 1);
        const had = r?.url ? r.px || 0 : 0;
        // past the budget, the pictures of groups not drawn now (scrolled away) make room first
        for (const [k, other] of [...map]) {
          if (used - had + w * h <= RASTER_PIXELS) break;
          if (k !== key && !make.has(k) && other.url) { used -= other.px || 0; drop(k); }
        }
        if (cap.width < w || cap.height < h || used - had + w * h > RASTER_PIXELS) {
          // its paths show at this zoom; a picture made at another zoom goes
          if (r?.url) URL.revokeObjectURL(r.url);
          used -= had;
          map.set(key, { ink: g.ink, markup: r?.markup, scale, failed: true });
          continue;
        }
        let markup = r?.markup;
        if (!markup) {
          const [id, band] = key.split(":");
          if (lazyRef.current.has(id)) continue;   // its placeholders are not its picture
          const gEl = svg.querySelector(`g[data-ink-id="${CSS.escape(id)}"][data-ink-band="${band}"]`);
          if (!gEl || !gEl.childElementCount || gEl.querySelector("image.inkRaster")) continue;
          const xml = new XMLSerializer();
          markup = [...svg.children].filter((n) => n.localName === "defs").map((n) => xml.serializeToString(n)).join("")
            + xml.serializeToString(gEl);
        }
        const rec = { ink: g.ink, markup, scale, box, px: w * h, url: r?.url || null, working: true };
        used += w * h - had;
        map.set(key, rec);
        const doc = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="${box.join(" ")}" preserveAspectRatio="none">${markup}</svg>`;
        const svgUrl = URL.createObjectURL(new Blob([doc], { type: "image/svg+xml" }));
        const done = (url) => {
          if (rec.dead || map.get(key) !== rec) { if (url) URL.revokeObjectURL(url); return; }
          if (rec.url && rec.url !== url) URL.revokeObjectURL(rec.url);
          Object.assign(rec, { url, working: false, failed: !url });
          setTick((n) => n + 1);
        };
        const img = new Image();
        img.onload = () => {
          try {
            const canvas = document.createElement("canvas");
            canvas.width = w; canvas.height = h;
            canvas.getContext("2d").drawImage(img, 0, 0, w, h);
            canvas.toBlob((png) => { URL.revokeObjectURL(svgUrl); done(png ? URL.createObjectURL(png) : null); }, "image/png");
          } catch { done(svgUrl); }   // a canvas the image taints: the SVG image itself is the picture
        };
        img.onerror = () => { URL.revokeObjectURL(svgUrl); done(null); };
        img.src = svgUrl;
      }
    }, RASTER_QUIET_MS);
    return () => clearTimeout(timer);
  });
  return (key, ink) => {
    const r = store.current.get(key);
    return r && r.ink === ink && r.url && !busy.has(key.split(":")[0]) ? r : null;
  };
}

// The box around `samples`, padded by `pad` (page units); null for none.
function samplesBox(samples, pad) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const s of samples) {
    if (s.x < x0) x0 = s.x;
    if (s.x > x1) x1 = s.x;
    if (s.y < y0) y0 = s.y;
    if (s.y > y1) y1 = s.y;
  }
  return x0 === Infinity ? null : [x0 - pad, y0 - pad, x1 + pad, y1 + pad];
}

// tool: the armed tool {tool: pen|highlighter|eraser|select, color, size}
// or null; penTool: what a stylus draws with when nothing is armed;
// eraserMode: "stroke" (whole strokes) | "partial" (cuts through them),
// eraserSize its S/M/L index; lassoMode: "free" (a drawn loop) | "box";
// blocks: this page's ink blocks; selection: {page, items: [{id, ids}]};
// flash: {id, nonce} outlines a group briefly.
// onFingerScroll: a finger dragged with a tool armed and pen only on, and
// scrolled instead of drawing. eraserFilter: what an erasure may remove
// ('all' | 'pen' | 'highlighter', for the hover glyph and the scribble);
// drawHold, scribble: the account's draw-and-hold and scribble-to-erase
// flags; fingerUndo: 'off' | 'tap' | 'double'. onEraseEnd: the eraser tool
// lifted; onEraseMany(page, [{gid, ids}]): a scribble's targets;
// onUndoGesture('undo' | 'redo'): a finger chord; onHoldStatus({text, once}):
// the pill a snapped stroke raises. clip: the handwriting clipboard holds
// strokes — a pen or mouse held still with a writing tool, or a mouse
// right-click on blank page with a tool armed, then offers "Paste here",
// which calls onPaste(page, pt, {width, height}); keys: {copy, cut,
// duplicate}, the chord labels the selection menu's titles name.
// onInkLive(page, msg): a chunk of the stroke in progress for the page's
// other peers (inkLive.js; App sends it on the page socket); their strokes
// in progress on this surface, the wet strokes, come from inkLive's store.
export function InkLayer({ pageNumber, wrapRef, width, height, blocks, tool, penTool, penOnly, pressure, eraserMode,
  eraserSize = 1, eraserFilter = "all", drawHold = false, scribble = false, fingerUndo = "off", lassoMode = "free", selection, flash,
  clip = false, keys,
  onStroke, onErase, onErasePartial, onEraseEnd, onEraseMany, onSelect, onAction, onMoveSelection, onJump, onPaste,
  onFingerScroll, onUndoGesture, onHoldStatus, onInkFeatures, onInkLive }) {
  useInkVersion();
  const canvasRef = useRef(null);
  const wetRef = useRef(null);
  const svgRef = useRef(null);
  // the id of the last stroke this layer committed: its group is the one
  // being written, which keeps its paths on a dense page
  const lastStrokeRef = useRef(null);
  const cursorRef = useRef(null);
  const refreshCursorRef = useRef(null);
  const [dragOffset, setDragOffset] = useState(null);   // while moving the selection: {dx, dy} in pt
  const [transform, setTransform] = useState(null);
  // The one-row "Paste here" menu: {x, y} on screen and `pt` on the page.
  const [pasteMenu, setPasteMenu] = useState(null);
  useLayoutEffect(() => { setTransform(null); }, [selection]);
  const live = useRef({});
  // Whether a pen is writing on this layer, the page's own stroke or one
  // the native host took (the pointer effect sets it; the finger chords ask).
  const penBusy = useRef(() => false);

  const groups = [];
  for (const b of blocks || []) {
    // the drawing shown so far while a newer file of the group loads
    const ink = inkStore.inkShown(b);
    if (!ink?.strokes?.length) continue;
    groups.push({ id: b.id, ink });
  }
  const grain = useGrain(groups.map((g) => g.ink));
  // A drawing from a newer build (a tool, a brush, a key or a channel
  // this one does not know) draws plainly; say so, once per drawing. The
  // handler sets the app's status, so it runs after the render, not in it.
  const onInkFeaturesRef = useRef(onInkFeatures);
  onInkFeaturesRef.current = onInkFeatures;
  useEffect(() => {
    if (!onInkFeaturesRef.current) return;
    for (const g of groups) {
      if (featured.has(g.ink)) continue;
      featured.add(g.ink);
      const features = inkFeatures(g.ink);
      if (features) onInkFeaturesRef.current(features);
    }
  });
  // A replay this surface takes part in (replay/useFrame.js): one of its
  // groups' own, its sheet's, or a recording its handwriting was written
  // during; each group then draws as it stood at the replay's instant.
  const replay = useGroupFrame(groups, pageNumber);
  const frame = frameOf(replay);
  // This page's selection: the ids per group and their box.
  const sel = selection && selection.page === pageNumber ? selection : null;
  const selectedIds = new Set();
  let selBox = null;
  if (sel) {
    for (const item of sel.items) {
      const g = groups.find((x) => x.id === item.id);
      if (!g) continue;
      item.ids.forEach((id) => selectedIds.add(id));
      selBox = unionBox(selBox, boundsOf(g.ink, item.ids));
    }
  }
  live.current = { tool, penTool, penOnly, pressure, eraserMode, eraserSize, eraserFilter, drawHold, scribble, fingerUndo, lassoMode,
    width, height, groups, selBox, replay, clip, penBusy: () => penBusy.current(),
    onStroke, onErase, onErasePartial, onEraseEnd, onEraseMany, onSelect, onMoveSelection, onJump, onPaste, onFingerScroll, onUndoGesture,
    onHoldStatus, onInkLive };
  useWetStrokes(pageNumber, wrapRef, wetRef, live);
  // Dense pages ("dense pages" above): culling past IDLE_STROKES strokes on
  // the surface, outlines built in idle time, and past RASTER_STROKES the
  // finished groups as pictures — not the group this layer's last stroke
  // went into, nor one with a selection or in a replay.
  const total = groups.reduce((n, g) => n + g.ink.strokes.length, 0);
  const shown = useVisibleGroups(wrapRef, groups, width, height, total > IDLE_STROKES);
  useIdleOutlines(groups, shown);
  const busy = new Set();
  if (total > RASTER_STROKES) {
    for (const item of sel?.items || []) busy.add(item.id);
    for (const g of groups) {
      if (frame && groupInScope(replay, g.id, g.ink, pageNumber)) busy.add(g.id);
      else if (lastStrokeRef.current && g.ink.strokes.some((s) => s.id === lastStrokeRef.current)) busy.add(g.id);
    }
  }
  const lazy = new Set(total > IDLE_STROKES ? groups.filter((g) => lazyGroup(g.ink)).map((g) => g.id) : []);
  const raster = useGroupRasters(svgRef, wrapRef, groups, width, total > RASTER_STROKES, busy, shown, lazy);

  // The finger chords: this layer is a host while it can undo (an editable
  // surface); the document-level detector is wired while any host is.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || !onUndoGesture) return undefined;
    chordHosts.set(el, live);
    wireChords();
    return () => {
      chordHosts.delete(el);
      if (!chordHosts.size) chordWiring?.();
    };
  }, [wrapRef, !!onUndoGesture]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    let drawing = null; // {mode: stroke|erase|lasso|move, …}
    // The pointer the native host took from this page (claimForHost): its
    // stroke arrives as a message, its own events are ignored, and fingers
    // stay palms until it ends.
    let hostOwned = null;
    penBusy.current = () => drawing?.pointerType === "pen" || hostOwned != null;
    let pending = null; // A possible tap/hold; swipes retain native scrolling.
    let pasteHold = null; // the pointer whose hold opened "Paste here": its lift is not a click
    const contacts = new Set();
    let hover = null;
    const hideCursor = () => {
      hover = null;
      if (cursorRef.current) cursorRef.current.style.display = "none";
      el.classList.remove("inkHovering");
    };
    const showCursor = (e) => {
      if (e.pointerType === "touch" || e.target.closest?.(`.inkSelectionHit, ${NOT_INK}`)) { hideCursor(); return; }
      const L = live.current, use = L.tool || (e.pointerType === "pen" ? L.penTool : null);
      if (!use || !L.width || !cursorRef.current) { hideCursor(); return; }
      const rect = el.getBoundingClientRect(), k = rect.width / L.width;
      const x = e.clientX - rect.left, y = e.clientY - rect.top;
      if (x < 0 || y < 0 || x > rect.width || y > rect.height) { hideCursor(); return; }
      const eraser = use.tool === "eraser" || !!(e.buttons & 34);
      const diameter = eraser ? 2 * (ERASER_SIZES[L.eraserSize] ?? ERASER_SIZES[1])
        : use.tool === "select" ? 12 : use.size * k;
      const cursor = cursorRef.current;
      cursor.dataset.tool = eraser ? "eraser" : use.tool;
      // With an eraser filter on, the ring carries the kind it erases.
      if (eraser && L.eraserFilter && L.eraserFilter !== "all") cursor.dataset.filter = L.eraserFilter;
      else delete cursor.dataset.filter;
      // A pen's footprint follows its brush: a round dot, a teardrop for a
      // brush pen, and for a pencil an ellipse leaning the way the pencil
      // does when the pointer reports tilt, as wide as the pencil's law
      // draws at that slant (finishStroke.js).
      const brush = !eraser && use.tool === "pen" ? brushValue(use.brush) : null;
      if (brush) cursor.dataset.brush = brush; else delete cursor.dataset.brush;
      const tilt = brush === "pencil" && e.pointerType === "pen" ? penTilt(e) : null;
      const major = tilt ? use.size * k * widthLaw({ tool: "pen", brush: "pencil" })({ p: 0.5, a: tilt.a }) : diameter;
      const minor = tilt ? major * Math.max(0.4, Math.sin(tilt.a * Math.PI / 180)) : diameter;
      Object.assign(cursor.style, { display: "block", left: `${x}px`, top: `${y}px`,
        width: `${Math.max(1, major)}px`, height: `${Math.max(1, minor)}px`,
        transform: tilt ? `translate(-50%, -50%) rotate(${tilt.z}deg)` : "" });
      el.classList.add("inkHovering");
      hover = e;
    };
    // Tool changes and zooms update a stationary pointer as well.
    refreshCursorRef.current = () => { if (hover) showCursor(hover); };
    const clearPending = (revert = false) => {
      clearTimeout(pending?.timer);
      if (revert && pending?.shown) live.current.onSelect?.(pageNumber, []);
      pending = null;
    };
    const trackDown = (e) => {
      contacts.add(e.pointerId);
      if (pending && pending.id !== e.pointerId) clearPending(true);
      if (e.pointerType === "pen") fingerHint = false;
      // A second finger makes a finger's stroke a pinch: the stroke goes.
      if (drawing?.pointerType === "touch" && e.pointerType === "touch" && drawing.id !== e.pointerId) {
        finish({ pointerId: drawing.id }, true);
      }
    };
    const trackUp = (e) => contacts.delete(e.pointerId);

    const setup = (e, forcedTool) => {
      const L = live.current;
      if (!L.width) return null;
      let use = forcedTool || L.tool;
      if (!use) {
        if (!(L.penTool && e.pointerType === "pen")) return null;
        use = L.penTool;
      }
      if (!forcedTool && L.penOnly && e.pointerType === "touch") return null;
      if (e.button !== 0 && !(e.pointerType === "pen" && (e.buttons & 34))) return null;
      const rect = el.getBoundingClientRect();
      const k = rect.width / L.width;                             // css px per pt
      const eraser = use.tool === "eraser" || !!(e.buttons & 32) || !!(e.buttons & 2);
      // The eraser tool returns to the last tool on its lift (onEraseEnd);
      // the pen's eraser end and barrel button never do.
      return { use, eraser, eraseSource: use.tool === "eraser" ? "tool" : "barrel", rect, k, pointerType: e.pointerType,
        toPt: (ev) => ({ x: (ev.clientX - rect.left) / k, y: (ev.clientY - rect.top) / k }) };
    };
    // A pointer sample into the draw context: through the preset's
    // stabilizer when it has one (the brush position comes back, or nothing
    // while the pointer is within the string), else as it is. `ending` is
    // the lift's pressure rule (inkInput.js); `lift` appends the
    // stabilizer's catch-up to the lift point.
    const pushSample = (d, s) => {
      const previous = d.samples.at(-1);
      if (previous && s.x === previous.x && s.y === previous.y && s.p === previous.p) return;
      d.samples.push(s);
    };
    const takeSample = (d, ev, ending = false, lift = ending) => {
      if (!d.stab) { appendInkSample(d, ev, ending); return; }
      const s = inkSample(d, ev, ending);
      if (lift) d.stab.finish(s).forEach((q) => pushSample(d, q));
      else { const q = d.stab.push(s); if (q) pushSample(d, q); }
    };
    // Draw and hold. snapHold runs the recogniser over the stroke so far
    // (lines only for a highlighter) and, on a hit, puts the shape on the
    // draw context: the canvas then paints it instead of the trace (two
    // frames in the selection blue), and the tip's further moves refit it
    // from the snap's params, the tip's position at the snap as `from`.
    // Under smoothing the samples end at the brush, which trails the tip by
    // the string's length: the tip itself closes the recognised path, so a
    // held line ends where the pen rests, not short of it.
    const snapHold = (d, shift) => {
      const tip = d.tip || d.samples.at(-1);
      const path = d.stab && d.tip ? [...d.samples, { ...d.samples.at(-1), x: tip.x, y: tip.y }] : d.samples;
      const hit = recognise(path, { k: d.k, kinds: d.kinds });
      if (!hit) return false;
      d.hold = { kind: hit.kind, params: hit.params, snap: hit.params, from: { x: tip.x, y: tip.y }, at: performance.now(),
        flash: 2, shift, preview: null };
      d.predicted = [];
      clearTimeout(d.predictionTimer);
      if (!d.raf) d.raf = requestAnimationFrame(paint);
      return true;
    };
    // A pen or mouse held still from its down, before it drew PASTE_MOVE css
    // px, with strokes on the clipboard: the draft goes (nothing commits)
    // and "Paste here" opens at the point. Fingers never arm it (a resting
    // palm must not paste); the click the lift would deliver is swallowed.
    const pasteHere = (d) => {
      if (drawing !== d) return;
      drawing = null;
      pasteHold = d.id;
      try { el.releasePointerCapture(d.id); } catch { /* already released */ }
      if (d.raf) cancelAnimationFrame(d.raf);
      clearTimeout(d.predictionTimer);
      hideCanvas();
      liveEnd(d);
      setPasteMenu({ x: d.downAt.x, y: d.downAt.y, pt: d.toPt({ clientX: d.downAt.x, clientY: d.downAt.y }) });
    };
    const holdFire = (d) => {
      d.holdTimer = 0;
      if (drawing !== d || d.hold) return;
      // the paste hold: no travel, against the shape hold's chord — a tip
      // that has travelled cannot paste, one that has not cannot snap
      if (d.pasteArmed && d.travel <= PASTE_MOVE) { pasteHere(d); return; }
      if (!d.holdOn || d.samples.length < 2) return;
      const a = d.samples[0], b = d.samples.at(-1);
      if (Math.hypot(b.x - a.x, b.y - a.y) < Math.max(24 / d.k, 18)) return;
      if (d.tail.at - d.startTime < HOLD_MIN_MS) return;
      snapHold(d, false);
    };
    // Per move: refit or cancel a held shape, else (re)arm the timer when
    // the tip moved; Shift with a mouse recognises at once.
    const trackHold = (d, e) => {
      const tip = d.tip;
      if (d.hold) {
        const h = d.hold;
        const moved = Math.hypot(tip.x - h.from.x, tip.y - h.from.y) * d.k;
        if (!h.shift && moved > CANCEL_PX && performance.now() - h.at <= CANCEL_MS) { d.hold = null; return; }
        // Once the tip has gone REFIT_PX out, every move refits, so a tip
        // coming back to the snap brings the shape back with it.
        if (moved >= REFIT_PX) h.refitting = true;
        if (h.refitting) {
          h.params = refitShape(h.kind, h.snap, { pointer: tip, from: h.from, shift: !!e.shiftKey });
          h.preview = null;
        }
        return;
      }
      if (!d.holdOn) return;
      if (e.pointerType === "mouse" && e.shiftKey) {
        const since = d.shiftAt ? Math.hypot(e.clientX - d.shiftAt.x, e.clientY - d.shiftAt.y) : Infinity;
        if (since >= 4) {
          d.shiftAt = { x: e.clientX, y: e.clientY };
          if (snapHold(d, true)) return;
        }
      }
      if (Math.hypot(e.clientX - d.tail.x, e.clientY - d.tail.y) > HOLD_MOVE) {
        d.tail = { x: e.clientX, y: e.clientY, at: e.timeStamp };
        clearTimeout(d.holdTimer);
        d.holdTimer = setTimeout(() => holdFire(d), HOLD_MS);
      }
    };

    const eraseUnder = (ctx, ev) => {
      const { x, y } = ctx.toPt(ev);
      const L = live.current;
      const r = (ERASER_SIZES[L.eraserSize] ?? ERASER_SIZES[1]) / ctx.k;
      for (const g of L.groups) {
        if (L.eraserMode === "partial") { L.onErasePartial?.(pageNumber, g.id, x, y, r); continue; }
        const ids = hitStrokes(g.ink, x, y, r);
        if (ids.length) L.onErase?.(pageNumber, g.id, ids);
      }
    };

    // The live canvas's context in page units. `dirty` (a box in page
    // units): clear only that part — the stroke's frames clear the union of
    // the last frame's box and this one's (dirty rects) — else all of it.
    const canvasCtx = (d, dirty = null) => {
      const canvas = canvasRef.current;
      if (!canvas) return null;
      const ctx = canvas.getContext("2d", { desynchronized: true });
      if (!ctx) return null;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (!dirty) ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(canvas.width / d.rect.width * d.k, 0, 0, canvas.height / d.rect.height * d.k, 0, 0);
      if (dirty) ctx.clearRect(dirty[0], dirty[1], dirty[2] - dirty[0], dirty[3] - dirty[1]);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      ctx.setLineDash([]);
      return ctx;
    };
    const paint = () => {
      const d = drawing;
      if (!d) return;
      d.raf = 0;
      if (d.mode !== "lasso") { paintStroke(d); return; }
      const ctx = canvasCtx(d);
      if (!ctx) return;
      ctx.strokeStyle = "rgba(80, 140, 255, 0.95)";
      ctx.lineWidth = 1 / d.k;
      ctx.setLineDash([4 / d.k, 3 / d.k]);
      ctx.beginPath();
      d.poly.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.closePath();
      ctx.stroke();
    };
    // A frame of the stroke in progress: the trace (or the held shape) in
    // its preset's look (paintLiveStroke), clearing only the dirty rect.
    const paintStroke = (d) => {
      const { use } = d;
      // A held shape paints in the trace's place: an even line (the committed
      // stroke is pen: false), the selection blue for its first two frames.
      const hold = d.hold;
      let flash = false;
      if (hold) {
        if (!hold.preview) hold.preview = shapeSamples(hold.kind, hold.params, { span: 0, p: 0.5 });
        flash = hold.flash > 0;
        if (flash) hold.flash--;
      }
      const samples = hold ? hold.preview : [...d.samples, ...(d.predicted || [])];
      // Every width a brush draws stays within two sizes of its centre line.
      const pad = 2 * (use.size || 1) + 2 / d.k;
      let box = samplesBox(samples, pad);
      if (!hold && d.level === 4 && d.tip) box = unionBox(box, [d.tip.x - pad, d.tip.y - pad, d.tip.x + pad, d.tip.y + pad]);
      const ctx = canvasCtx(d, unionBox(d.lastBox, box) || [0, 0, 0, 0]);
      d.lastBox = box;
      if (!ctx) return;
      // A held shape draws as it commits (finishStroke's hold stage): pen
      // false and a shape, so constant width whatever the brush — no law.
      const look = d.look || use;
      paintLiveStroke(ctx, samples, hold ? { ...look, pen: false, shape: hold.kind } : look,
        { law: hold ? null : d.law, color: flash ? SNAP_BLUE : use.color, flash, done: !!hold });
      if (flash && !d.raf) d.raf = requestAnimationFrame(paint);
      // At the longest string, a faint line from the brush to the pointer
      // shows what the smoothing is doing.
      if (!hold && d.level === 4 && d.tip && d.samples.length) {
        const last = d.samples.at(-1);
        ctx.globalAlpha = 0.4;
        ctx.globalCompositeOperation = "source-over";
        ctx.strokeStyle = "#888";
        ctx.lineWidth = 1 / d.k;
        ctx.beginPath();
        ctx.moveTo(last.x, last.y);
        ctx.lineTo(d.tip.x, d.tip.y);
        ctx.stroke();
      }
    };
    const showCanvas = (ctx) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const size = canvasSize(ctx.rect.width, ctx.rect.height, window.devicePixelRatio || 1);
      canvas.width = 0; canvas.height = size.height; canvas.width = size.width;
      canvas.style.display = "block";
    };
    const hideCanvas = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = 0; canvas.height = 0;
      canvas.style.display = "none";
    };

    // Live co-drawing (inkLive.js, collab.md "ink-live"): the stroke's id is
    // minted at the down, and its samples go to the page's other peers as
    // they are written, a chunk at most every LIVE_MS; the lift sends the
    // rest marked `done` once the stroke is committed under that id, and
    // anything that ends it uncommitted (a cancel, a scribble that erased,
    // the paste hold, the native host taking the pen) sends `drop`.
    const liveStart = (d) => {
      d.liveId = strokeId();
      if (!live.current.onInkLive) return;
      // the look the stroke commits with (a stylus with the pressure
      // preference off writes as a mouse does: pen false, no pressure)
      const look = d.look || d.use, pen = d.look ? d.look.pen : d.pen;
      d.live = { sent: 0, timer: 0, last: -Infinity, style: inkLive.liveStyle(look, pen), k: Math.round(d.k * 1000) / 1000 };
      liveTick(d);
    };
    const liveChunk = (d, done) => {
      const lv = d.live, samples = inkLive.liveSamples(d.samples, lv.sent, lv.style.pen);
      const msg = { id: d.liveId, style: lv.style, k: lv.k, n: lv.sent, samples };
      lv.sent += samples.length;
      if (done && lv.sent >= d.samples.length) msg.done = true;
      live.current.onInkLive?.(pageNumber, msg);
      return samples.length;
    };
    const liveSend = (d) => {
      const lv = d.live;
      if (!lv) return;
      lv.timer = 0;
      if (lv.sent >= d.samples.length) return;
      liveChunk(d, false);
      lv.last = performance.now();
      liveTick(d);
    };
    const liveTick = (d) => {
      const lv = d.live;
      if (!lv || lv.timer || d.samples.length <= lv.sent) return;
      lv.timer = setTimeout(() => liveSend(d), Math.max(0, inkLive.LIVE_MS - (performance.now() - lv.last)));
    };
    // The stroke ended: committed, the samples not sent yet go (the last
    // chunk, possibly empty, marked done); else a `drop` when peers have
    // seen any of it.
    const liveEnd = (d) => {
      if (d.liveCommitted) lastStrokeRef.current = d.liveId;
      const lv = d.live;
      if (!lv) return;
      clearTimeout(lv.timer);
      if (d.liveCommitted) {
        do liveChunk(d, true); while (lv.sent < d.samples.length);
      } else if (lv.sent) live.current.onInkLive?.(pageNumber, { id: d.liveId, drop: true });
      d.live = null;
    };

    // A finished stroke's samples (page units) through finishStroke (the
    // held shape, a straight highlight, a scribble that erases, else
    // decimated with the widths baked) to the page's ink.
    const commitStroke = (d) => {
      if (!d.samples.length) return false;
      // the preset's look, a stylus with the pressure preference off writing
      // like a mouse (pen false: an even line, the Brush's speed law)
      const L = live.current, { use } = d, pen = d.look ? d.look.pen : d.pen;
      const style = { tool: use.tool, brush: use.brush, dash: use.dash, color: use.color, size: use.size, opacity: use.opacity ?? 1, pen };
      const result = finishStroke(d.samples, { style, pen, k: d.k,
        hold: d.hold ? { kind: d.hold.kind, params: d.hold.params } : null, straight: use.straight,
        scribble: !!L.scribble, groups: L.groups, keep: eraserKeep(L.eraserFilter) });
      const { commit, erase, status } = result;
      const look = result.style || style;
      if (erase?.length) {
        if (L.onEraseMany) L.onEraseMany(pageNumber, erase);
        else for (const { gid, ids } of erase) if (ids.length) L.onErase?.(pageNumber, gid, ids);
      }
      if (status) L.onHoldStatus?.(status);
      if (!commit?.length) return !!erase?.length;
      const stroke = encodeStroke({ ...look, id: d.liveId, t0: d.t0, samples: commit, ch: strokeChannels(look, commit) });
      d.liveCommitted = true;
      L.onStroke?.(pageNumber, stroke, { width: L.width, height: L.height });
      return true;
    };
    // The host took the draft's pointer: the draft goes now, without saving,
    // whatever events WebKit sends that pointer afterwards (a pointercancel,
    // a pointerup, or more moves), so a stroke is never saved twice. The
    // pointer capture stays, so its events keep coming here to be ignored.
    const handOver = (d) => {
      if (drawing !== d) return;
      drawing = null;
      hostOwned = d.id;
      if (d.raf) cancelAnimationFrame(d.raf);
      clearTimeout(d.predictionTimer);
      clearTimeout(d.holdTimer);
      hideCanvas();
      liveEnd(d);
    };
    // A pen stroke under a host that captures the Pencil: claim it (with
    // where it went down, which tells the host which touch it is) and keep
    // drawing the draft until the host answers. Taken, the draft goes and
    // the host's samples commit instead; left, the stroke ends here. The
    // host's samples are viewport CSS px and ms from the first, sampled as
    // the pointer path samples, with the rect and zoom of the down.
    const claimForHost = (d, e) => {
      const claim = {
        taken: false,
        // The host's raw samples go through the same stabilizer as the
        // page's own; never a hold (the shell sends them at the lift only).
        commit: (samples) => {
          handOver(d);
          if (hostOwned === d.id) hostOwned = null;
          const c = { ...d, samples: [], startTime: 0, hold: null, tip: null, stab: d.level ? makeStabilizer(d.level, { k: d.k }) : null };
          samples.forEach(([x, y, p, t], i) =>
            takeSample(c, { clientX: x, clientY: y, pressure: p, timeStamp: t }, false, i === samples.length - 1));
          return commitStroke(c);
        },
      };
      hostClaims.set(d.id, claim);
      const drop = () => { if (hostClaims.get(d.id) === claim) hostClaims.delete(d.id); };
      const { use } = d;
      // size: the width on screen, in CSS px (the host's points)
      postToHost({ type: "claim", pointerId: d.id, at: { x: e.clientX, y: e.clientY },
        tool: { kind: use.tool, color: use.color, size: use.size * d.k, opacity: use.opacity ?? 1 } })
        .then((reply) => {
          if (hostClaims.get(d.id) !== claim) return; // the page already ended it
          if (!reply?.taken) { drop(); return; }
          claim.taken = true;
          handOver(d);
        }, drop);
    };

    const onDown = (e) => {
      if (e.target.closest?.(NOT_INK)) { hideCursor(); return; }
      showCursor(e);
      const L = live.current;
      const selectionDrag = !!L.onSelect && e.target.closest?.(".inkSelectionHit")
        && (e.pointerType === "touch" || !L.tool || L.tool.tool === "select") && e.pointerType !== "pen";
      // While the host draws a stroke, fingers are palms; a new pen down
      // means that stroke is over.
      if (hostOwned != null) {
        if (e.pointerType !== "pen") { e.preventDefault(); e.stopPropagation(); return; }
        hostOwned = null;
      }
      const ctx = setup(e, selectionDrag ? { tool: "select" } : null);
      // A pen takes priority even if the palm landed first in finger-draw mode.
      if (drawing?.pointerType === "touch" && e.pointerType === "pen" && ctx) {
        finish({ pointerId: drawing.id }, true);
      }
      if (drawing) {
        if (drawing.pointerType === "pen" && e.pointerType === "touch") {
          e.preventDefault();
          e.stopPropagation();
        }
        return;
      }
      if (contacts.size > 1 && e.pointerType === "touch") return;
      // A read-only page: a tap near a stroke shows its note, below the
      // page's own clickable marks.
      const passive = !L.onSelect && !!L.onJump;
      if (passive && e.target.closest?.(ABOVE_PASSIVE_INK)) return;
      const navigation = passive || (e.pointerType === "touch" ? (L.penOnly || !L.tool) : e.pointerType === "mouse" && !L.tool);
      if (!selectionDrag && navigation && (L.onSelect || passive) && L.width && e.button === 0) {
        clearPending();
        const probe = setup(e, { tool: "select" });
        if (!probe) return;
        const pt = probe.toPt(e);
        const hit = nearestInkStroke(L.groups, pt.x, pt.y, (e.pointerType === "touch" ? 10 : 5) / probe.k);
        pending = { id: e.pointerId, x: e.clientX, y: e.clientY, hit, shown: false };
        if (hit && e.pointerType === "touch" && !passive) {
          pending.timer = setTimeout(() => {
            if (!pending || drawing) return;
            pending.shown = true;
            live.current.onSelect?.(pageNumber, [pending.hit]);
          }, 450);
        }
        // Leave default touch behavior intact so scrolling can cancel the tap.
        if (hit || L.selBox) e.stopPropagation();
        return;
      }
      if (!ctx) return;
      clearPending();
      e.preventDefault();
      e.stopPropagation();
      try { el.setPointerCapture(e.pointerId); } catch { /* capture is a nicety */ }
      if (ctx.eraser) {
        drawing = { ...ctx, id: e.pointerId, mode: "erase" };
        eraseUnder(ctx, e);
        return;
      }
      if (ctx.use.tool === "select") {
        const pt = ctx.toPt(e), box = live.current.selBox;
        if (box && pt.x >= box[0] && pt.x <= box[2] && pt.y >= box[1] && pt.y <= box[3]) {
          drawing = { ...ctx, id: e.pointerId, mode: "move", start: pt, dx: 0, dy: 0 };
          return;
        }
        showCanvas(ctx);
        drawing = { ...ctx, id: e.pointerId, mode: "lasso", box: live.current.lassoMode === "box", start: pt,
          poly: [[pt.x, pt.y]], raf: 0 };
        return;
      }
      showCanvas(ctx);
      const pen = e.pointerType === "pen";
      const level = ctx.use.stabilize || 0;
      drawing = { ...ctx, id: e.pointerId, mode: "stroke", samples: [], t0: Date.now(),
        startTime: e.timeStamp, pressure: live.current.pressure, pen, raf: 0,
        // the look the live canvas draws and the stroke commits with: a
        // stylus with the pressure preference off has no pressure to read
        look: { ...ctx.use, pen: pen && !!live.current.pressure },
        law: widthLaw({ tool: ctx.use.tool, brush: ctx.use.brush, pen: pen && !!live.current.pressure }),
        // smoothing (stabilizer.js) and draw-and-hold (shapes.js), both
        // snapshotted at the down like the pressure preference
        level, stab: level ? makeStabilizer(level, { k: ctx.k }) : null, tip: null,
        hold: null, holdTimer: 0, holdOn: !!L.drawHold && !(ctx.use.tool === "highlighter" && ctx.use.straight === "off"),
        kinds: ctx.use.tool === "highlighter" ? ["line"] : undefined,
        tail: { x: e.clientX, y: e.clientY, at: e.timeStamp }, downAt: { x: e.clientX, y: e.clientY },
        // the paste hold (pasteHere): a pen or mouse, strokes on the clipboard
        pasteArmed: !!L.clip && !!L.onPaste && e.pointerType !== "touch", travel: 0,
        // A finger's stroke clears the selection once it has moved 8 css px
        // (as a tap is told from a swipe), so a two-finger chord, whose
        // first finger starts a stroke the second cancels, never clears the
        // selection it is about to restore.
        deselect: e.pointerType === "touch" };
      if (drawing.pasteArmed) { const d = drawing; d.holdTimer = setTimeout(() => holdFire(d), HOLD_MS); }
      if (!drawing.deselect) L.onSelect?.(pageNumber, []);
      takeSample(drawing, e);
      if (drawing.pen && nativeHost()?.ink) claimForHost(drawing, e);
      else liveStart(drawing);
      paint();
    };
    const onMove = (e) => {
      showCursor(e);
      if (pending?.id === e.pointerId && Math.hypot(e.clientX - pending.x, e.clientY - pending.y) > 8) clearPending(true);
      const d = drawing;
      if (!d || e.pointerId !== d.id) return;
      e.preventDefault();
      if (d.mode === "erase") { eraseUnder(d, e); return; }
      if (d.mode === "move") {
        const pt = d.toPt(e);
        d.dx = pt.x - d.start.x;
        d.dy = pt.y - d.start.y;
        if (!d.raf) d.raf = requestAnimationFrame(() => { d.raf = 0; setDragOffset({ dx: d.dx, dy: d.dy }); });
        return;
      }
      const events = e.getCoalescedEvents?.() || [];
      for (const ev of events.length ? events : [e]) {
        if (d.mode === "lasso") {
          const pt = d.toPt(ev);
          // A box is the rectangle from the start to the pointer, as a polygon.
          if (d.box) d.poly = [[d.start.x, d.start.y], [pt.x, d.start.y], [pt.x, pt.y], [d.start.x, pt.y]];
          else d.poly.push([pt.x, pt.y]);
        } else takeSample(d, ev);
      }
      if (d.mode === "stroke") {
        liveTick(d);
        d.tip = d.toPt(e);
        d.travel = Math.max(d.travel || 0, Math.hypot(e.clientX - d.downAt.x, e.clientY - d.downAt.y));
        if (d.deselect && Math.hypot(e.clientX - d.downAt.x, e.clientY - d.downAt.y) >= 8) {
          d.deselect = false;
          live.current.onSelect?.(pageNumber, []);
        }
        trackHold(d, e);
        // No prediction under smoothing: the raw predicted positions would
        // spike from the brush out to the pointer, the shake the line hides.
        d.predicted = d.hold || d.stab ? [] : predictedInkSamples(d, e);
        clearTimeout(d.predictionTimer);
        if (d.predicted.length) d.predictionTimer = setTimeout(() => {
          if (drawing !== d) return;
          d.predicted = [];
          if (!d.raf) d.raf = requestAnimationFrame(paint);
        }, 32);
      }
      if (!d.raf) d.raf = requestAnimationFrame(paint);
    };
    const finish = (e, cancelled) => {
      if (pending?.id === e.pointerId) {
        const p = pending;
        const isTap = !cancelled && Math.hypot(e.clientX - p.x, e.clientY - p.y) <= 8;
        clearPending(cancelled);
        if (isTap) {
          const L = live.current;
          if (p.hit && L.replay?.scope.recording && seekToStroke(L.groups, p.hit)) {
            e.preventDefault(); e.stopPropagation(); swallowClick();
            return;
          }
          if (L.onSelect) L.onSelect(pageNumber, p.hit ? [p.hit] : []);
          else if (p.hit) L.onJump?.(p.hit.id);
          if (p.hit || L.selBox) { e.preventDefault(); e.stopPropagation(); swallowClick(); }
        }
        return;
      }
      const d = drawing;
      if (!d || e.pointerId !== d.id) return;
      drawing = null;
      try { el.releasePointerCapture(e.pointerId); } catch { /* already released */ }
      if (d.raf) cancelAnimationFrame(d.raf);
      clearTimeout(d.predictionTimer);
      clearTimeout(d.holdTimer);
      const L = live.current;
      if (d.mode === "erase") {
        if (cancelled) return;
        swallowClick();
        // The eraser tool lifted (whether or not it erased): App may return
        // to the last tool. The pen's eraser end and barrel never do.
        if (d.eraseSource === "tool") L.onEraseEnd?.();
        return;
      }
      if (d.mode === "move") {
        setDragOffset(null);
        if (!cancelled) {
          const pt = d.toPt(e);
          d.dx = pt.x - d.start.x; d.dy = pt.y - d.start.y;
        }
        if (!cancelled && (Math.abs(d.dx) > 0.5 || Math.abs(d.dy) > 0.5)) L.onMoveSelection?.(pageNumber, d.dx, d.dy);
        swallowClick();
        return;
      }
      hideCanvas();
      if (cancelled) { liveEnd(d); return; }
      swallowClick();
      if (d.mode === "lasso") {
        const items = [];
        if (d.poly.every(([x, y]) => Math.hypot(x - d.start.x, y - d.start.y) * d.k <= 8)) {
          const hit = nearestInkStroke(L.groups, d.start.x, d.start.y, 10 / d.k);
          if (hit) items.push(hit);
        } else if (d.poly.length >= 3) {
          for (const g of L.groups) {
            const ids = strokesInLasso(g.ink, d.poly);
            if (ids.length) items.push({ id: g.id, ids });
          }
        }
        L.onSelect?.(pageNumber, items);
        return;
      }
      if (!d.samples.length) { liveEnd(d); return; }
      if (d.deselect) { d.deselect = false; L.onSelect?.(pageNumber, []); }
      takeSample(d, e, true);
      commitStroke(d);
      liveEnd(d);
    };
    // A claim the host did not take ends with the page's own pointer-up; a
    // taken one ends with the host's stroke message, whenever that comes.
    const onUp = (e) => {
      if (e.pointerId === hostOwned) hostOwned = null;
      if (!hostClaims.get(e.pointerId)?.taken) hostClaims.delete(e.pointerId);
      if (e.pointerId === pasteHold) { pasteHold = null; e.preventDefault(); swallowClick(); return; }
      finish(e, false);
    };
    const onCancel = (e) => { contacts.delete(e.pointerId); finish(e, true); hideCursor(); };
    // iPad Safari can pan with Pencil even after pointerdown.preventDefault().
    // Cancel its matching touch gesture before scrolling cancels the pointer
    // stream. Keep touch-action available for finger scrolling and pinch zoom.
    const onTouch = (e) => {
      const L = live.current;
      if (!L.width || !(L.tool || L.penTool)) return;
      const pencil = Array.from(e.changedTouches).some((t) =>
        t.touchType === "stylus" || (!t.touchType && drawing?.pointerType === "pen"));
      // While the pen is down, direct contacts are palms, not pan/pinch.
      // As soon as it lifts, fingers can navigate again.
      if (e.touches.length > 1) clearPending(true);
      // One finger moving over a page with a tool armed scrolls it when only
      // a pen draws, which nothing on screen explains.
      if (fingerHint && e.type === "touchmove" && !pencil && !drawing && e.touches.length === 1
        && L.tool && L.penOnly && L.onFingerScroll) {
        fingerHint = false;
        L.onFingerScroll();
      }
      if (!pencil && drawing?.pointerType !== "pen" && drawing?.mode !== "move" && hostOwned == null) return;
      if (e.cancelable) e.preventDefault();
      e.stopPropagation(); // Do not feed Pencil into the viewer's pan/pinch handlers.
    };
    el.addEventListener("touchstart", onTouch, { capture: true, passive: false });
    el.addEventListener("touchmove", onTouch, { capture: true, passive: false });
    el.addEventListener("pointerdown", onDown, true);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", hideCursor);
    const resizeObserver = new ResizeObserver(() => { if (hover) showCursor(hover); });
    resizeObserver.observe(el);
    window.addEventListener("scroll", hideCursor, true);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onCancel);
    el.addEventListener("lostpointercapture", onCancel);
    // A right-click on blank page with a tool armed and strokes on the
    // clipboard opens "Paste here"; over ink, the selection or without a
    // clipboard the page's own menus run (prevented only over a hit).
    const onContext = (e) => {
      if (pending?.hit || e.target.closest?.(".inkSelectionHit")) { e.preventDefault(); return; }
      const L = live.current;
      if (e.button !== 2 || !L.tool || !L.clip || !L.onPaste || !L.width || drawing) return;
      const rect = el.getBoundingClientRect(), k = rect.width / L.width;
      const pt = { x: (e.clientX - rect.left) / k, y: (e.clientY - rect.top) / k };
      if (nearestInkStroke(L.groups, pt.x, pt.y, 5 / k)) return;
      e.preventDefault();
      e.stopPropagation();
      setPasteMenu({ x: e.clientX, y: e.clientY, pt });
    };
    const onBlur = () => { hideCursor(); clearPending(true); contacts.clear(); if (drawing) finish({ pointerId: drawing.id }, true); };
    el.addEventListener("contextmenu", onContext);
    document.addEventListener("pointerdown", trackDown, true);
    document.addEventListener("pointerup", trackUp, true);
    document.addEventListener("pointercancel", trackUp, true);
    window.addEventListener("blur", onBlur);
    return () => {
      el.removeEventListener("touchstart", onTouch, true);
      el.removeEventListener("touchmove", onTouch, true);
      el.removeEventListener("pointerdown", onDown, true);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", hideCursor);
      window.removeEventListener("scroll", hideCursor, true);
      resizeObserver.disconnect();
      hideCursor();
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onCancel);
      el.removeEventListener("lostpointercapture", onCancel);
      window.removeEventListener("blur", onBlur);
      el.removeEventListener("contextmenu", onContext);
      document.removeEventListener("pointerdown", trackDown, true);
      document.removeEventListener("pointerup", trackUp, true);
      document.removeEventListener("pointercancel", trackUp, true);
      clearPending();
      if (drawing?.raf) cancelAnimationFrame(drawing.raf);
      clearTimeout(drawing?.predictionTimer);
      clearTimeout(drawing?.holdTimer);
      if (drawing) {
        try { el.releasePointerCapture(drawing.id); } catch { /* already released */ }
        liveEnd(drawing);
      }
      hideCanvas();
    };
  }, [wrapRef, pageNumber]);

  useEffect(() => { refreshCursorRef.current?.(); }, [tool, penTool, eraserSize, eraserFilter]);

  if (!width || !height) return null;
  const flashGroup = flash && groups.find((g) => g.id === flash.id);
  const fb = flashGroup ? inkBounds(flashGroup.ink) : null;
  const armed = !!tool;
  const dragging = !!((dragOffset || transform) && selectedIds.size);
  const previewTransform = transform
    ? `translate(${transform.cx} ${transform.cy}) rotate(${transform.angle * 180 / Math.PI}) scale(${transform.scale}) translate(${-transform.cx} ${-transform.cy})`
    : `translate(${dragOffset?.dx || 0} ${dragOffset?.dy || 0})`;
  // Controls follow the same preview as the ink, but retain their screen
  // size. Keep selBox unchanged: gesture math uses the original geometry.
  const previewPoint = (x, y) => transform ? transformPoint(x, y, transform)
    : [x + (dragOffset?.dx || 0), y + (dragOffset?.dy || 0)];
  const handlePositions = selBox ? {
    resize: previewPoint(selBox[2], selBox[3]), rotate: previewPoint(selBox[2], selBox[1]),
  } : null;
  const hitCorners = selBox ? [[selBox[0] - 4, selBox[1] - 4], [selBox[2] + 4, selBox[1] - 4],
    [selBox[2] + 4, selBox[3] + 4], [selBox[0] - 4, selBox[3] + 4]].map(([x, y]) => previewPoint(x, y)) : null;
  const hitBox = hitCorners ? [Math.min(...hitCorners.map(([x]) => x)), Math.min(...hitCorners.map(([, y]) => y)),
    Math.max(...hitCorners.map(([x]) => x)), Math.max(...hitCorners.map(([, y]) => y))] : null;
  return (
    <GrainContext.Provider value={grain.fill}>
      <svg ref={svgRef} className="inkLayer" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        {grain.defs}
        {/* every group's band-0 strokes (highlighters) under every group's ink; on a dense
            page a group off the view is left empty and a finished one drawn as its picture */}
        {[0, 1].map((band) => groups.map((g) => {
          if (!g.ink.strokes.some((s) => strokeBand(s) === band)) return null;
          const pic = shown && !shown.has(g.id) ? null : raster(`${g.id}:${band}`, g.ink);
          return (
            <g key={`${g.id}:${band}`} data-ink-id={g.id} data-ink-band={band}
              style={{ pointerEvents: armed || !onSelect ? "none" : "visiblePainted", cursor: "pointer" }}>
              {shown && !shown.has(g.id) ? null : pic ? (
                // the picture takes no presses: an image is hit over its whole box, the paths
                // on their ink only, and a tap selects from the samples (nearestInkStroke)
                <image className="inkRaster" href={pic.url} x={pic.box[0]} y={pic.box[1]} width={pic.box[2]} height={pic.box[3]}
                  preserveAspectRatio="none" style={band === 0 ? { mixBlendMode: "multiply", pointerEvents: "none" } : { pointerEvents: "none" }} />
              ) : (
                <ReplayStrokes ink={g.ink} band={band} frame={frame && groupInScope(replay, g.id, g.ink, pageNumber) ? frame : null}
                  hide={dragging ? selectedIds : null} lazy={lazy.has(g.id)} />
              )}
            </g>
          );
        }))}
        {dragging ? (
          <g transform={previewTransform}>
            {groups.map((g) => (
              <Strokes key={g.id} ink={{ strokes: g.ink.strokes.filter((s) => selectedIds.has(s.id)) }} />
            ))}
          </g>
        ) : null}
        {selBox ? (
          <rect className="inkSelRect" data-ink-selection="true"
            transform={previewTransform}
            x={selBox[0] - 4} y={selBox[1] - 4}
            width={selBox[2] - selBox[0] + 8} height={selBox[3] - selBox[1] + 8} rx={3} />
        ) : null}
        {fb ? (
          <rect key={flash.nonce} className="inkFlash" x={fb[0] - 6} y={fb[1] - 6}
            width={fb[2] - fb[0] + 12} height={fb[3] - fb[1] + 12} rx={4} />
        ) : null}
      </svg>
      {selBox && onSelect ? <div className="inkSelectionHit" data-guide="ink.selection" aria-label={t("Move selected handwriting")}
        style={{ left: `${hitBox[0] / width * 100}%`, top: `${hitBox[1] / height * 100}%`,
          width: `${(hitBox[2] - hitBox[0]) / width * 100}%`, height: `${(hitBox[3] - hitBox[1]) / height * 100}%` }} /> : null}
      {selBox && onAction && !dragging ? <InkSelectionMenu wrapRef={wrapRef} box={selBox} width={width}
        strokes={groups.flatMap((g) => g.ink.strokes.filter((s) => selectedIds.has(s.id)))}
        canMerge={canMergeSelection(groups, selectedIds)} keys={keys}
        onAction={onAction} onClose={() => onSelect(pageNumber, [])} /> : null}
      {pasteMenu && onPaste ? (
        <ContextMenu x={pasteMenu.x} y={pasteMenu.y} className="inkPasteMenu" onClose={() => setPasteMenu(null)}>
          <MenuItem icon={ClipboardIcon} onClick={() => { setPasteMenu(null); onPaste(pageNumber, pasteMenu.pt, { width, height }); }}>
            {t("Paste here")}
          </MenuItem>
        </ContextMenu>
      ) : null}
      {selBox && onAction ? <InkTransformHandles wrapRef={wrapRef} box={selBox} width={width} height={height}
        positions={handlePositions}
        maxScale={Math.min(10, MAX_STROKE_SIZE / Math.max(...groups.flatMap((g) => g.ink.strokes.filter((s) => selectedIds.has(s.id)).map(strokeMaxWidth))))}
        onPreview={setTransform} onCommit={(value) => onAction("transform", value)} /> : null}
      <canvas ref={wetRef} className="inkWetCanvas" />
      <canvas ref={canvasRef} className="inkCanvas" />
      <div ref={cursorRef} className="inkCursor" aria-hidden="true">
        <span />
        {/* the eraser filter's kind, inside the ring when a filter is on (data-filter) */}
        <PenIcon className="inkCursorGlyph inkCursorGlyph-pen" size={9} />
        <HighlightIcon className="inkCursorGlyph inkCursorGlyph-highlighter" size={9} />
      </div>
    </GrainContext.Provider>
  );
}

function InkTransformHandles({ wrapRef, box, width, height, positions, maxScale, onPreview, onCommit }) {
  const drag = useRef(null);
  const cancel = () => { drag.current = null; onPreview(null); };
  useEffect(() => {
    const escape = (e) => { if (e.key === "Escape") cancel(); };
    window.addEventListener("blur", cancel);
    window.addEventListener("keydown", escape);
    return () => { drag.current = null; window.removeEventListener("blur", cancel); window.removeEventListener("keydown", escape); };
  }, []);
  const valueAt = (e) => {
    const d = drag.current;
    const x = (e.clientX - d.rect.left) / d.k - d.cx, y = (e.clientY - d.rect.top) / d.k - d.cy;
    let angle = Math.atan2(y, x) - d.startAngle;
    angle = Math.atan2(Math.sin(angle), Math.cos(angle));
    if (e.shiftKey) angle = Math.round(angle / (Math.PI / 12)) * Math.PI / 12;
    return { cx: d.cx, cy: d.cy, angle: d.mode === "rotate" ? angle : 0,
      scale: d.mode === "resize" ? Math.max(0.1, Math.min(d.maxScale, Math.hypot(x, y) / d.radius)) : 1 };
  };
  const begin = (e, mode) => {
    if (e.button !== 0 || drag.current) return;
    e.preventDefault(); e.stopPropagation();
    const rect = wrapRef.current.getBoundingClientRect(), k = rect.width / width;
    const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
    const x = (e.clientX - rect.left) / k - cx, y = (e.clientY - rect.top) / k - cy;
    drag.current = { id: e.pointerId, mode, rect, k, cx, cy, radius: Math.max(0.01, Math.hypot(x, y)), startAngle: Math.atan2(y, x), maxScale };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    if (drag.current?.id !== e.pointerId) return;
    e.preventDefault(); e.stopPropagation(); onPreview(valueAt(e));
  };
  const finish = (e) => {
    if (drag.current?.id !== e.pointerId) return;
    e.preventDefault(); e.stopPropagation();
    const value = valueAt(e);
    cancel();
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (Math.abs(value.scale - 1) > 0.001 || Math.abs(value.angle) > 0.001) onCommit(value);
  };
  return <>
    {["resize", "rotate"].map((mode) => <button key={mode} type="button" className={`inkTransformHandle inkTransform-${mode}`}
      aria-label={mode === "resize" ? t("Resize selected handwriting") : t("Rotate selected handwriting")}
      title={mode === "resize" ? t("Drag to resize; arrow keys change size") : t("Drag to rotate; hold Shift to snap to 15°; arrow keys rotate")}
      style={{ left: `${positions[mode][0] / width * 100}%`, top: `${positions[mode][1] / height * 100}%` }}
      onPointerDown={(e) => begin(e, mode)} onPointerMove={move} onPointerUp={finish}
      onPointerCancel={cancel} onLostPointerCapture={cancel} onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
        e.preventDefault(); e.stopPropagation();
        const sign = e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : 1;
        onCommit({ cx: (box[0] + box[2]) / 2, cy: (box[1] + box[3]) / 2,
          scale: mode === "resize" ? Math.min(maxScale, sign > 0 ? 1.1 : 1 / 1.1) : 1,
          angle: mode === "rotate" ? sign * Math.PI / 12 : 0 });
      }}>{mode === "resize" ? <ResizeIcon /> : <RedoIcon />}</button>)}
  </>;
}

// A picker's swatches ({hex, alpha}, shared/model/palette.js pickerColors)
// and a custom colour, the current `color` (hex) marked; onColor(hex,
// alpha) — a swatch brings its alpha, the custom picker keeps the current
// one. A pen preset's colours (markup/MarkupToolbar.jsx), the handwriting
// selection menu and a text box's (markup/TextBoxLayer.jsx
// TextStyleChoices, which ignores the alpha).
export function ColorChoices({ colors, color, alpha, onColor }) {
  const current = String(color || "").toLowerCase();
  const listed = colors.some((c) => c.hex === current);
  return <>
    {colors.map((c, i) => (
      <button key={c.hex + c.alpha + i} type="button" className={"colorBtn inkSwatch" + (current === c.hex ? " selected" : "")}
        style={{ background: swatchCss(c) }} onClick={() => onColor(c.hex, c.alpha)} title={inkColorName(c.hex)} aria-label={inkColorName(c.hex)}
        aria-pressed={current === c.hex} />
    ))}
    <label className={"colorBtn inkSwatch inkCustomColor" + (listed ? "" : " selected")}
      title={t("Custom color")} style={{ "--ink-custom": color }}>
      <input type="color" value={color} aria-label={t("Custom color")} onChange={(e) => onColor(e.target.value.toLowerCase(), alpha)} />
    </label>
  </>;
}

// Native title tooltips are inconsistent for Pencil hover. Use the same
// descriptions for mouse, pen and keyboard focus, outside clipped toolbars.
// A tip goes below its button unless that is near the window's bottom;
// `above` puts every tip above, for a strip whose menus open upward (the
// recording player's), so a tip and the menu its button opens share a side.
export function InkTooltips({ children, above = false, ...props }) {
  const id = useId(), [tip, setTip] = useState(null), active = useRef(null), timer = useRef(null), hushed = useRef(null);
  const clear = () => { clearTimeout(timer.current); active.current?.removeAttribute("aria-describedby"); active.current = null; setTip(null); };
  // A press takes the tooltip away and keeps it away while the pointer stays
  // on that button: otherwise the smallest move of the hand brings it back,
  // on top of whatever the press has just opened.
  const press = (e) => { clear(); hushed.current = e.target.closest("button, label[title]"); };
  const leave = () => { hushed.current = null; clear(); };
  useEffect(() => {
    window.addEventListener("scroll", clear, true);
    window.addEventListener("blur", clear);
    return () => { clearTimeout(timer.current); window.removeEventListener("scroll", clear, true); window.removeEventListener("blur", clear); };
  }, []);
  const show = (e) => {
    if (e.pointerType === "touch") return;
    const target = e.target.closest("button, label[title]");
    if (!target || target === active.current || target === hushed.current) return;
    hushed.current = null;
    // A button whose menu stands open needs no tooltip; it would cover it.
    if (target.getAttribute("aria-expanded") === "true") return;
    clear(); active.current = target;
    const description = target.title || target.getAttribute("aria-label");
    if (!description) return;
    timer.current = setTimeout(() => {
      const r = target.getBoundingClientRect();
      target.setAttribute("aria-describedby", id);
      setTip({ text: description, x: Math.max(8, Math.min(r.left, window.innerWidth - 288)),
        y: r.bottom + 8, above: above || r.bottom > window.innerHeight - 100, top: r.top - 8 });
    }, e.type === "focus" ? 0 : 250);
  };
  return <><div {...props} onPointerOver={show} onPointerLeave={leave} onFocus={show} onBlur={clear}
    onPointerDownCapture={press}>{children}</div>
    {tip ? createPortal(<div id={id} role="tooltip" className="inkTooltip" style={{ left: tip.x, top: tip.above ? tip.top : tip.y,
      transform: tip.above ? "translateY(-100%)" : undefined }}>{tip.text}</div>, document.body) : null}</>;
}

// The box an ink layer is seen through: a PDF's viewer, else the nearest
// ancestor that scrolls (a notebook's viewer, the notes), else the window.
function viewportOf(el) {
  const pdf = el.closest(".pdfViewer");
  if (pdf) return pdf;
  for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
    const { overflowY } = getComputedStyle(n);
    if (overflowY === "auto" || overflowY === "scroll") return n;
  }
  return null;
}

// Where a selection's menu goes (the handwriting's, a text box's): above
// the selection when it fits in the scroller the page is seen through (and
// below the tool strip floating over its top), else below it, else
// wherever it fits; null while the selection is off-screen. The anchor
// says where and the ContextMenu, which knows its own height, picks the
// side (placement.js); a height guessed here would put it on one side for
// a frame and then the other.
// It follows scrolls and resizes, in the frame they happen. `ref` is an
// element the selection moves and scales with (observed for resizes);
// rectOf(its screen rect) is the selection's screen rect, and `deps` what
// else moves it. Spread the anchor on the ContextMenu.
export function useSelectionMenuAnchor(ref, rectOf, deps) {
  const [anchor, setAnchor] = useState(null);
  const rectRef = useRef(rectOf);
  rectRef.current = rectOf;
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const view = viewportOf(el);
    const update = (now) => {
      const viewport = view ? view.getBoundingClientRect()
        : { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
      const bar = document.querySelector(".pdfInkBar")?.getBoundingClientRect();
      const roof = bar && bar.bottom > viewport.top && bar.top < viewport.bottom ? Math.max(viewport.top, bar.bottom) : viewport.top;
      const { left, top, right, bottom } = rectRef.current(el.getBoundingClientRect());
      const next = bottom < viewport.top || top > viewport.bottom || right < viewport.left || left > viewport.right ? null
        : { x: Math.max(viewport.left + 8, left), y: top - 40, anchorBottom: true, flipY: bottom + 40, bounds: { top: roof, bottom: viewport.bottom } };
      const set = () => setAnchor((prev) => JSON.stringify(prev) === JSON.stringify(next) ? prev : next);
      if (now) flushSync(set); else set();
    };
    update(false);
    const again = () => update(true);
    const observer = new ResizeObserver(again);
    observer.observe(el);
    window.addEventListener("scroll", again, true);
    window.addEventListener("resize", again);
    return () => { observer.disconnect(); window.removeEventListener("scroll", again, true); window.removeEventListener("resize", again); };
  }, [ref, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps
  return anchor;
}

// Whether the selection can be merged into another note: the selected ids
// are exactly one group's strokes, and the surface has another group with
// strokes (an emptied captioned block is not one, as App's merge sees it).
function canMergeSelection(groups, selectedIds) {
  if (!selectedIds.size) return false;
  const full = groups.filter((g) => g.ink.strokes.length);
  if (full.length < 2) return false;
  const holders = full.filter((g) => g.ink.strokes.some((s) => selectedIds.has(s.id)));
  return holders.length === 1 && holders[0].ink.strokes.length === selectedIds.size
    && holders[0].ink.strokes.every((s) => selectedIds.has(s.id));
}

// Whether the selection menu's second row (More edits) stands open: kept
// for the session, so a reader who works from it finds it open again.
let moreOpen = false;

// The selection's menu: two rows of 36 px buttons, at most seven each (a
// hard cap for every later addition), the first fixed — Color, Width, Style
// (greyed until the pen styles land), Copy, Duplicate, More, Delete — and
// More adding the second: Cut, Flip horizontal, Flip vertical, New note,
// Merge into note (`canMerge` only), Select note, Show note. The Color and
// Width rows open under whichever rows show, one at a time. `keys` names
// the chords the titles carry ("Copy · Ctrl+C"; the label alone unbound).
function InkSelectionMenu({ wrapRef, box, width, strokes, canMerge = false, keys, onAction, onClose }) {
  const [options, setOptions] = useState(null);
  const [more, setMoreState] = useState(moreOpen);
  // The Style row's brush menu: {x, y} under its picker while open.
  const [styleMenu, setStyleMenu] = useState(null);
  const styleBtnRef = useRef(null);
  const setMore = (on) => { moreOpen = on; setMoreState(on); };
  const palette = usePalette();
  const [x0, y0, x1, y1] = box;
  const anchor = useSelectionMenuAnchor(wrapRef, (r) => {
    const k = r.width / width;
    return { left: r.left + x0 * k, top: r.top + y0 * k, right: r.left + x1 * k, bottom: r.top + y1 * k };
  }, [width, x0, y0, x1, y1]);
  if (!anchor) return null;
  // 12 screen pixels, in the page's points
  const nudge = () => 12 * width / wrapRef.current.getBoundingClientRect().width;
  const kinds = [...new Set(strokes.map((s) => s.tool))];
  const withKey = (label, key) => (key ? `${label} · ${key}` : label);
  // The Style row: the brush the selected pens share (undefined: mixed;
  // highlighters take no brush) and the line every selected stroke shares
  // (undefined: mixed, so no pill is pressed).
  const pens = strokes.filter((s) => (s.tool || "pen") === "pen");
  const penBrush = pens.length && pens.every((s) => brushValue(s.brush) === brushValue(pens[0].brush)) ? brushValue(pens[0].brush) : undefined;
  const line = strokes.length && strokes.every((s) => (s.dash ?? null) === (strokes[0].dash ?? null)) ? strokes[0].dash ?? null : undefined;
  const toggleOptions = (row) => { setStyleMenu(null); setOptions(options === row ? null : row); };
  const toggleStyleMenu = (button) => {
    styleBtnRef.current = button;
    const r = button.getBoundingClientRect();
    setStyleMenu((open) => (open ? null : { x: r.left, y: r.bottom + 4 }));
  };
  return <ContextMenu {...anchor} ignoreRef={wrapRef} onClose={onClose} className="inkEditMenu">
    <InkTooltips role="toolbar" aria-label={t("Edit handwriting")} onPointerDown={(e) => e.stopPropagation()}>
      <div className="inkEditRow">
        <button className={"ctlBtn" + (options === "color" ? " modeActive" : "")} aria-label={t("Color")} title={t("Color")} aria-expanded={options === "color"} onClick={() => toggleOptions("color")}><PaletteIcon aria-hidden="true" /></button>
        <button className={"ctlBtn" + (options === "width" ? " modeActive" : "")} aria-label={t("Width")} title={t("Width")} aria-expanded={options === "width"} onClick={() => toggleOptions("width")}><LineWidthIcon aria-hidden="true" /></button>
        <button className={"ctlBtn" + (options === "style" ? " modeActive" : "")} aria-label={t("Style")} title={t("Style")} aria-expanded={options === "style"} onClick={() => toggleOptions("style")}><BrushIcon aria-hidden="true" /></button>
        <button className="ctlBtn" aria-label={t("Copy")} title={withKey(t("Copy"), keys?.copy)} onClick={() => onAction("copy")}><CopyIcon aria-hidden="true" /></button>
        <button className="ctlBtn" aria-label={t("Duplicate")} title={withKey(t("Duplicate"), keys?.duplicate)} onClick={() => onAction("duplicate", { dx: nudge(), dy: nudge() })}><DuplicateIcon aria-hidden="true" /></button>
        <button className={"ctlBtn" + (more ? " modeActive" : "")} aria-label={t("More")} title={t("More edits")} aria-expanded={more} onClick={() => setMore(!more)}><MoreIcon aria-hidden="true" /></button>
        <button className="ctlBtn inkDeleteBtn" aria-label={t("Delete")} title={t("Delete selected handwriting")} onClick={() => onAction("delete")}><TrashIcon aria-hidden="true" /></button>
      </div>
      {more ? <div className="inkEditOptions inkEditMore" aria-label={t("More edits")}>
        <button className="ctlBtn" aria-label={t("Cut")} title={withKey(t("Cut"), keys?.cut)} onClick={() => onAction("cut")}><ScissorsIcon aria-hidden="true" /></button>
        <button className="ctlBtn" aria-label={t("Flip horizontal")} title={t("Flip horizontal: mirror left to right")} onClick={() => onAction("flip", { axis: "h" })}><FlipHIcon aria-hidden="true" /></button>
        <button className="ctlBtn" aria-label={t("Flip vertical")} title={t("Flip vertical: mirror top to bottom")} onClick={() => onAction("flip", { axis: "v" })}><FlipVIcon aria-hidden="true" /></button>
        <button className="ctlBtn" aria-label={t("New note")} title={t("New note from the selected handwriting")} onClick={() => onAction("split")}><NotebookPenIcon aria-hidden="true" /></button>
        {canMerge ? <button className="ctlBtn" aria-label={t("Merge into note")} title={t("Merge this note's handwriting into the previous note on this page")} onClick={() => onAction("merge")}><MergeIcon aria-hidden="true" /></button> : null}
        <button className="ctlBtn" aria-label={t("Select note")} title={t("Select all handwriting in this note")} onClick={() => onAction("select-note")}><RectSelectIcon aria-hidden="true" /></button>
        <button className="ctlBtn" aria-label={t("Show note")} title={t("Show note")} onClick={() => onAction("show-note")}><FileTextIcon aria-hidden="true" /></button>
      </div> : null}
      {options === "color" ? <div className="inkEditOptions" aria-label={t("Selected ink color")}>
        {pickerColors(palette, kinds.every((k) => k === "highlighter") ? "highlighter" : "pen").map((c, i) =>
          <button key={c.hex + c.alpha + i} className="colorBtn inkSwatch" style={{ background: swatchCss(c) }} title={inkColorName(c.hex)} aria-label={inkColorName(c.hex)}
            aria-pressed={strokes.every((s) => s.color === c.hex)} onClick={() => onAction("style", { color: c.hex, opacity: c.alpha })} />)}
        <label className="colorBtn inkSwatch inkCustomColor" title={t("Custom color")}><input type="color" aria-label={t("Selected ink custom color")} value={strokes[0]?.color || INK_COLORS[0]}
          onChange={(e) => onAction("style", { color: e.target.value })} /></label>
      </div> : null}
      {options === "width" ? kinds.map((kind) => <div className="inkEditOptions" key={kind} aria-label={t("{kind} width", { kind: inkKindName(kind) })}>
        {kinds.length > 1 ? (kind === "highlighter" ? <HighlightIcon aria-label={t("Highlighter")} /> : <PenIcon aria-label={t("Pen")} />) : null}
        {sizesFor(kind).map((size, i) => <button key={size} className={"ctlBtn inkSizeBtn" + (strokes.filter((s) => s.tool === kind).every((s) => s.size === size) ? " modeActive" : "")} aria-label={t("{kind} width {size} pt", { kind: inkKindName(kind), size: size })} title={t("{kind} width {size} pt", { kind: inkKindName(kind), size: size })}
          aria-pressed={strokes.filter((s) => s.tool === kind).every((s) => s.size === size)}
          onClick={() => onAction("style", { tool: kind, size })}><span className="inkSizeDot" aria-hidden="true" style={{ width: 4 + i * 2, height: 4 + i * 2, background: "currentColor" }} /></button>)}
      </div>) : null}
      {options === "style" ? <div className="inkEditOptions inkEditStyle" aria-label={t("Selected ink style")}>
        {pens.length ? <InkStylePicker brush={penBrush} mixed={penBrush === undefined} open={!!styleMenu} title={t("Pen style")} onOpen={toggleStyleMenu} /> : null}
        <InkLineChoices value={line} onLine={(dash) => onAction("style", { dash })} />
      </div> : null}
    </InkTooltips>
    {/* The brush menu, outside the InkTooltips host (InkStylePicker.jsx); a
        menu of this menu, so a press in it does not dismiss this one. */}
    {styleMenu && options === "style" && pens.length ? <InkStyleMenu x={styleMenu.x} y={styleMenu.y} value={penBrush ?? null} ignoreRef={styleBtnRef}
      onPick={(brush) => onAction("style", { brush: brush === "pen" ? null : brush })} onClose={() => setStyleMenu(null)} /> : null}
  </ContextMenu>;
}

// The replay's play / stop button.
export function InkReplayButton({ replay, className = "" }) {
  const label = replay.playing ? t("Stop replay") : t("Replay handwriting");
  return (
    <button type="button" className={"inkReplayBtn " + className} aria-label={label} aria-pressed={replay.playing}
      title={replay.playing ? t("Stop the replay") : t("Replay: watch the handwriting being written, stroke by stroke")}
      onClick={(e) => { e.stopPropagation(); if (replay.playing) replay.stop(); else replay.play(); }}>
      {replay.playing ? <StopIcon size={12} /> : <PlayIcon size={12} />}
    </button>
  );
}

// The group as a picture in the notes tree (same strokes, cropped to its
// box), its replay button in the corner: the replay plays on the page
// (scrolled to the group) and in the card, as does a recording the group
// was written during. Click: jump to it on the page.
export function InkCard({ block, onJump }) {
  useInkVersion();
  const ink = inkStore.inkShown(block);
  const replayState = useGroupFrame(ink ? [{ id: block.id, ink }] : [], null);
  const grain = useGrain(ink ? [ink] : []);
  const replay = {
    playing: replayState?.scope.group === block.id,
    play: () => { onJump?.(block.id); playback.play(compressedClock(ink), { group: block.id }); },
    stop: () => playback.stop(block.id),
  };
  const b = ink ? inkBounds(ink) : null;
  if (!ink) {
    return <div className="blockInkCard blockInkPending" title={t("Loading handwriting…")} />;
  }
  if (!b) return null;
  const pad = 6;
  const w = b[2] - b[0] + 2 * pad, h = b[3] - b[1] + 2 * pad;
  return (
    <span className="blockInkCardWrap">
      <svg className="blockInkCard" viewBox={`${b[0] - pad} ${b[1] - pad} ${w} ${h}`} width={w} height={h}
        role="img" aria-label={t("Handwriting")}
        onClick={onJump ? (e) => { e.stopPropagation(); onJump(block.id); } : undefined}>
        {grain.defs}
        <GrainContext.Provider value={grain.fill}>
          <ReplayStrokes ink={ink} frame={frameOf(replayState)} />
        </GrainContext.Provider>
      </svg>
      <InkReplayButton replay={replay} />
    </span>
  );
}
