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
import React, { createContext, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { ContextMenu, MenuItem } from "../shared/ui/Menus";
import { getStroke } from "perfect-freehand";
import {
  ClipboardIcon, CopyIcon, DuplicateIcon, FileTextIcon, FlipHIcon, FlipVIcon, HighlightIcon, LineWidthIcon, MergeIcon, MoreIcon, NotebookPenIcon,
  PaletteIcon, PenIcon, PlayIcon, RectSelectIcon, RedoIcon, ResizeIcon, ScissorsIcon, StopIcon, TrashIcon, BrushIcon, SparklesIcon,
  PaintBucketIcon,
} from "../shared/ui/Icons";
import {
  MAX_STROKE_SIZE, boundsOf, byBand, cachedStrokePath, dashDots, dashPattern, dashRuns, encodeStroke, eraserKeep, isDash,
  inkBounds, inkFeatures, inkUntil, lerpSample, nearestInkStroke, opacityBand, outlineOptions, outlinePressure, runOutline,
  sizesFor, strokeBand, strokeBounds, strokeId, strokeMaxWidth, strokePath, strokePlaceholder, strokeWalls, strokesInLasso, svgPathFromPoints,
  transformPoint, unionBox,
  CLOSED_SHAPES, DEFAULT_NIB, FILL_OPACITY, NIB_HAIR, encodedStyle, isClosedShape, nibOutline, strokeNib,
} from "./ink";
import { brushOpacities, brushWidths, finishStroke, strokeChannels, widthLaw } from "./finishStroke.js";
import { GRAIN_PX, GRAIN_UNITS, grainImage, grainUri } from "./grain.js";
import { InkLineChoices, InkStyleMenu, InkStylePicker, brushValue } from "./InkStylePicker.jsx";
import {
  CANCEL_MS, CANCEL_PX, HOLD_MIN_MS, HOLD_MOVE, HOLD_MS, PASTE_MOVE, REFIT_PX, followHold, holdQualifies, recognise,
  refitShape, shapeSamples, stillSince, dragHandle, reshapeStroke, shapeHandles,
} from "./shapes.js";
import { claimTool, hostEvent, shapeFromHost, shapeToHost } from "./hostInk.js";
import {
  RULER_PX, hostSnap, moveRuler, onRail, pinchRuler, railLine, rulerDir, rulerEnds, rulerTicks, snapAngle, snapRail, stepAngle,
  stretchRuler, turnRuler,
} from "./ruler.js";
import { toolShape } from "./shapeTool.js";
import { doublePress } from "../shared/ui/press.js";
import { makeStabilizer } from "./stabilizer.js";
import { magForWidth, moveWindow, setMag } from "./zoomWindow.js";
import { makeChordDetector } from "./fingerChords.js";
import { INK_COLORS, colorName, pickerColors, swatchCss } from "../shared/model/palette.js";
import { usePalette } from "../shared/ui/paletteContext.js";
import * as inkStore from "./inkStore";
import * as playback from "../replay/playback.js";
import { compressedClock } from "../replay/clock.js";
import { frameOf, groupInScope, useGroupFrame } from "../replay/useFrame.js";
import { appendInkSample, inkSample, penTilt, penTwist, predictedInkSamples } from "./inkInput.js";
import * as inkLive from "./inkLive.js";
import { canvasSize, canvasWindow, windowHolds, windowMatrix, windowUnion } from "../shared/lib/canvasSize.js";
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
// away: the menus (portalled), the lasso's handles, the ruler's knob and
// ends, the zoom window's band and corner, a text box's width handle, its
// move band and its open editor (markup/TextBoxLayer.jsx), and the comment
// cards beside a PDF page (comments/CommentMargin.jsx). The ruler's band is
// the pointer's kind's: a pen about to write writes along it, any other
// pointer moves it (writesOnBand).
const BESIDE_PAGE = ".commentMargin";
const NOT_INK = `.inkEditMenu, .inkTransformHandle, .inkRulerKnob, .inkRulerEnd, .inkZoomBand, .inkZoomResize, .textBoxWidth, .textBoxBand, .textBox.editing, ${BESIDE_PAGE}`;
// The tools that write a stroke: a pen or a highlighter preset (the
// stylus's pen with nothing armed included).
const strokeTool = (use) => use?.tool === "pen" || use?.tool === "highlighter";
// Whether a pointer pressed on the ruler's band writes along it: a pen (not
// its eraser end or barrel) with a stroke tool. With the eraser, the lasso
// or the Shapes tool armed, and for a finger or the mouse, the band is the
// ruler's, which moves.
const writesOnBand = (L, e) => e.pointerType === "pen" && !(e.buttons & 34) && strokeTool(L.tool || L.penTool);
// Every position a pointer move carries: its coalesced events, else the move itself.
const coalesced = (e) => {
  const events = e.getCoalescedEvents?.() || [];
  return events.length ? events : [e];
};
// What takes a click above a stroke of a read-only page (whose tap shows
// the stroke's note): the page's own clickable marks. The strokes are drawn
// above them all, text boxes included (app.css), but take no pointer.
const ABOVE_PASSIVE_INK = "[data-hl-id], .pdfNoteAnchor, .pdfLinkBox, .textBox";
// Under a native host that captures the Pencil (shared/lib/host.js), a pen
// stroke is claimed at its down: the host takes the touch and draws the live
// line itself, then hands the finished samples back, and they commit here as
// the browser's own stroke would (docs/dev/ipad.md "The pen"). The claims
// by pointer id: {taken, commit(samples, held), hold(samples), held}. A
// taken claim always ends with a `stroke` message: the host sends one with
// no samples when the system cancels the touch, or after the page took a
// hold; `held` is the `n` of the hold whose shape the host drew.
const hostClaims = new Map();
if (nativeHost()?.ink) {
  onHostMessage("stroke", async ({ pointerId, samples, held }) => {
    const claim = hostClaims.get(pointerId);
    hostClaims.delete(pointerId);
    // No samples: the system cancelled the touch, and the claim just ends.
    const committed = !!claim?.commit(samples || [], held);
    // The reply drops the host's live line: wait for React's commit and a
    // paint, so the stroke never blinks out between the two.
    await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
    return { committed };
  });
  // Draw and hold under a host that says when its captured Pencil stays
  // still: the claim answers from the samples so far, at once ({} for a
  // pointer it no longer holds). Without `hold` nothing is recognised.
  if (nativeHost().hold) {
    onHostMessage("hold", ({ pointerId, samples }) => hostClaims.get(pointerId)?.hold(Array.isArray(samples) ? samples : []) ?? {});
  }
}
// Whether a stroke is in progress anywhere: a layer's pointer drawing (a
// stroke, an erasure, a lasso, a move) or a claim the host holds. App's
// Pencil gestures wait for it (pencilActions.js), so a double tap under the
// moving pen never undoes the stroke before it.
const busyLayers = new Set();
export function inkBusy() {
  if (hostClaims.size) return true;
  for (const busy of busyLayers) if (busy()) return true;
  return false;
}
// Whether a finger scrolling under an armed tool is still worth explaining
// (onFingerScroll): once a session, and never once a pen has drawn.
let fingerHint = true;
const SNAP_BLUE = "rgb(80, 140, 255)"; // a held shape's snap flash: .inkSelRect's stroke
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
const GrainContext = createContext(null);
const colorKey = (color) => String(color).toLowerCase().replace(/[^a-z0-9]/g, "");
function useGrain(inks) {
  const prefix = "inkGrain-" + useId().replace(/[^A-Za-z0-9_-]/g, "");
  const colors = new Set();
  for (const ink of inks) for (const s of ink?.strokes || []) if (s.brush === "pencil") colors.add(String(s.color || INK_COLORS[0]).toLowerCase());
  const key = [...colors].sort().join(" ");
  const fill = useMemo(() => {
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
  const grainFill = useContext(GrainContext);
  const strokes = band == null ? byBand(ink.strokes) : ink.strokes.filter((s) => strokeBand(s) === band);
  return strokes.map((s) => {
    if (hide?.has(s.id)) return null;
    // `lazy` (a dense group, "dense pages" below): a stroke whose outline is
    // still to be built stands in as its placeholder polyline meanwhile
    const p = lazy ? cachedStrokePath(s) || strokePlaceholder(s) : strokePath(s);
    const color = s.color || INK_COLORS[0], opacity = s.opacity ?? 1;
    const paint = (p.grain && grainFill?.(color)) || color;
    // A filled closed shape: its inside first, under its outline in the same band (ink.js strokePath's `fill`).
    const fill = p.fill && s.fill?.color
      ? <path key="fill" className="inkFill" d={p.fill} fill={s.fill.color} fillOpacity={s.fill.opacity ?? FILL_OPACITY} /> : null;
    if (p.bands) {
      return (
        <React.Fragment key={s.id}>
          {fill}
          {p.bands.map((b) => <path key={b.alpha} d={b.d} fill={paint} fillOpacity={b.alpha * opacity} />)}
        </React.Fragment>
      );
    }
    const line = p.stroke ? (
      <path key={fill ? "line" : s.id} d={p.d} fill="none" stroke={paint} strokeWidth={p.width} strokeOpacity={opacity}
        strokeLinecap="round" strokeLinejoin="round" strokeDasharray={p.dash ?? undefined}
        style={p.multiply ? { mixBlendMode: "multiply" } : undefined} />
    ) : (
      <path key={fill ? "line" : s.id} d={p.d} fill={paint} fillOpacity={opacity} />
    );
    return fill ? <React.Fragment key={s.id}>{fill}{line}</React.Fragment> : line;
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
// round dabs of its grain (paintPencil). A style with no width law
// (widthLaw) draws an even line at the nominal size: a held shape's
// preview among them, whose style carries its `shape` (a shape commits at
// constant width whatever its brush, with no tapers and no shading).
// `color` is the fill (the held shape's snap flash is blue); `flash` that
// flash's frames.
// A calligraphy pen drawn freehand is its nib (ink.js nibOutline over the
// samples so far: one Path2D filled once, nonzero, the shape it commits),
// its dash runs each a nib; and a closed shape with a `fill` (a Shapes drag
// with Fill on: `use.shape` its kind or record) has its inside filled
// first, under the line.
function paintLiveStroke(ctx, samples, use, { color = use.color, flash = false, done = false } = {}) {
  if (!samples.length) return;
  const dash = dashPattern(use), law = widthLaw(use);
  paintFill(ctx, samples, use);
  if (use.brush === "calligraphy" && use.tool !== "highlighter" && law !== null && use.dash !== "dotted") {
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = flash ? 1 : use.opacity ?? 1;
    ctx.fillStyle = color;
    const nib = Number.isFinite(use.nib) ? use.nib : DEFAULT_NIB;
    ctx.fill(new Path2D((dash ? dashRuns(samples, use) : [samples]).map((run) => nibOutline(run, use.size, nib)).join(" ")), "nonzero");
    return;
  }
  if (use.tool === "highlighter") {
    ctx.globalAlpha = flash ? 0.95 : use.opacity ?? 1;
    ctx.globalCompositeOperation = flash ? "source-over" : "multiply";
    strokeCentreline(ctx, samples, color, use.size, dash);
    return;
  }
  const ws = law === null ? null : brushWidths(samples, use, { live: !done });
  const os = law === null ? null : brushOpacities(samples, use);
  const pts = ws || os ? samples.map((s, i) => ({ ...s, ...(ws ? { w: ws[i] } : null), ...(os ? { o: os[i] } : null) })) : samples;
  const alpha = flash ? 1 : use.opacity ?? 1;
  ctx.globalCompositeOperation = "source-over";
  if (use.brush === "pencil" && !flash) { paintPencil(ctx, pts, use, color, alpha, dash); return; }
  ctx.globalAlpha = alpha;
  // a constant-width dashed line: its centreline stroked with the pattern, as the SVG draws it
  if (dash && !ws) { strokeCentreline(ctx, pts, color, use.size, dash); return; }
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
// A closed shape's inside on the live canvas: `use.fill` ({color,
// opacity?}) on a `use.shape` of a closed kind (CLOSED_SHAPES), the
// samples' polygon filled at the fill's opacity, as the SVG draws it.
function paintFill(ctx, samples, use) {
  const kind = typeof use.shape === "string" ? use.shape : use.shape?.kind;
  if (!use.fill?.color || !CLOSED_SHAPES.has(kind) || samples.length < 3) return;
  ctx.globalCompositeOperation = "source-over";
  ctx.globalAlpha = use.fill.opacity ?? FILL_OPACITY;
  ctx.fillStyle = use.fill.color;
  ctx.beginPath();
  samples.forEach((s, i) => (i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y)));
  ctx.closePath();
  ctx.fill();
}
// A constant-width line along the samples: its centreline stroked round,
// solid or with the dash pattern `dash`, as the SVG strokes it (a lone
// sample a dot).
function strokeCentreline(ctx, samples, color, width, dash) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = ctx.lineJoin = "round";
  ctx.setLineDash(dash || []);
  ctx.lineDashOffset = 0;
  ctx.beginPath();
  samples.forEach((s, i) => (i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y)));
  if (samples.length === 1) ctx.lineTo(samples[0].x + 0.01, samples[0].y);
  ctx.stroke();
  ctx.setLineDash([]);
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
// the committed stroke takes its place with no visible change. The canvas
// covers what shows of the surface (canvasWindow, as the live canvas does)
// and, while it has strokes, follows the scrolls and resizes that bring
// more of the surface into view. Its data-wet counts the wet strokes and
// data-wet-ids names them.
function useWetStrokes(surface, wrapRef, wetRef, live) {
  const paintRef = useRef(null);
  useEffect(() => {
    // `shown`: the window the canvas draws while it has strokes; `view`:
    // the box the surface is seen through, looked up once
    let raf = 0, shown = null, watching = false, view;
    const viewOf = (el) => (view === undefined ? (view = viewportOf(el)) : view);
    const schedule = () => { if (!raf) raf = requestAnimationFrame(paintWet); };
    const onView = () => {
      const el = wrapRef.current;
      if (raf || !el) return;
      const seen = canvasWindow(el.getBoundingClientRect(), viewRect(viewOf(el)));
      if (seen && !(shown && windowHolds(shown, seen))) schedule();
    };
    const watch = (on) => {
      if (on === watching) return;
      watching = on;
      if (on) { window.addEventListener("scroll", onView, true); window.addEventListener("resize", onView); }
      else { window.removeEventListener("scroll", onView, true); window.removeEventListener("resize", onView); }
    };
    const blank = (canvas) => {
      shown = null;
      if (canvas.width || canvas.style.display === "block") releaseCanvas(canvas);
    };
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
        blank(canvas);
        watch(false);
        canvas.dataset.wet = "0";
        canvas.dataset.wetIds = "";
        return;
      }
      canvas.dataset.wet = String(strokes.length);
      canvas.dataset.wetIds = strokes.map((s) => s.id).join(" ");
      watch(true);
      const win = canvasWindow(rect, viewRect(viewOf(el)));
      if (!win) { blank(canvas); return; }   // none of the surface shows: drawn once it does
      fitCanvas(canvas, win, rect);
      shown = win;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(...windowMatrix(canvas, win, rect.width / L.width));
      for (const s of strokes) {
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = "source-over";
        ctx.setLineDash([]);
        paintLiveStroke(ctx, s.samples, s.style, { done: s.done });
      }
    };
    paintRef.current = schedule;
    const off = inkLive.subscribeWet(surface, schedule);
    schedule();
    return () => { off(); cancelAnimationFrame(raf); watch(false); paintRef.current = null; };
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
const IDLE_STROKES = 200;             // a group past this mounts with placeholders
const IDLE_SLICE = 50;                // outlines built per idle slice
const RASTER_STROKES = 1500;          // a surface past this shows its finished groups as pictures
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
  // A layer mounted together with its surface (a view switched to with its
  // ink loaded) finds no surface below: React attaches the surface's ref
  // after its children's layout effects. `attached` runs the setup again
  // once the ref is there (the passive effect at the end).
  const [attached, setAttached] = useState(0);
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
        const v = viewRect(view);
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
  }, [enabled, wrapRef, width, height, attached]);
  // the groups changed (a stroke, a file): measured again before the paint
  useLayoutEffect(() => { measureRef.current?.(); });
  useEffect(() => {
    if (enabled && width && height && !measureRef.current && wrapRef.current) setAttached((n) => n + 1);
  });
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
  const store = useRef(new Map());   // key → {ink, markup, scale, box, px (device pixels), url, working}
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
          map.set(key, { ink: g.ink, markup: r?.markup, scale });
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
          Object.assign(rec, { url, working: false });
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
// flash: {id, nonce} outlines a group briefly. onStroke(page, stroke,
// {width, height}, {label}): a stroke committed, `label` its history
// entry's (finishStroke's: "ink stroke", "ink straight line", "ink shape").
// onFingerScroll: a finger dragged with a tool armed and pen only on, and
// scrolled instead of drawing. eraserFilter: what an erasure may remove
// ('all' | 'pen' | 'highlighter', for the hover glyph and the scribble);
// drawHold, scribble: the account's draw-and-hold and scribble-to-erase
// flags; fingerUndo: 'off' | 'tap' | 'double'. onEraseAlong(page, path, r,
// {mode, pass}): the eraser swept `path` (page points, from where the last
// call ended) with radius r (page units), once a frame and at the lift,
// `mode` 'partial' | 'stroke' and `pass` the id the gesture's calls share;
// onEraseEnd: the eraser tool lifted; onEraseMany(page, [{gid, ids}]): a
// scribble's targets;
// onUndoGesture('undo' | 'redo'): a finger chord; onHoldStatus({text, once}):
// the pill a snapped stroke raises. clip: the handwriting clipboard holds
// strokes — a pen or mouse held still with a writing tool, or a mouse
// right-click on blank page with a tool armed, then offers "Paste here",
// which calls onPaste(page, pt, {width, height}); keys: {copy, cut,
// duplicate}, the chord labels the selection menu's titles name.
// onInkLive(page, msg): a chunk of the stroke in progress for the page's
// other peers (inkLive.js; App sends it on the page socket); their strokes
// in progress on this surface, the wet strokes, come from inkLive's store.
// hoverPreview: the hover footprint shows the preset's look (this browser's
// inkHoverPreview), else a plain ring at its width. onHaptic(kind, at): a
// pen stroke snapped to a shape ('snap') or a pen lasso selected strokes
// ('select'), at a point in viewport css px — the iPad host's tick.
// ruler: the ruler when it lies on this surface ({surface, x, y, angle,
// length, snap}, ink/ruler.js), else null — drawn over the page (InkRuler)
// and written along; rulerUnits its ticks; onRuler(next | null) moves it.
// A `tool` of {tool: "shape", kind, fill, style} is the Shapes tool
// (ink/shapeTool.js): a drag draws its shape.
// chrome false: the zoom window's copy of the page (ink/ZoomWindow.jsx),
// which draws the strokes, the stroke in progress, peers' wet strokes and
// the selection's box (dragged as on the page) and writes as the page does,
// but renders no selection menu, no handles and no window box (the page's
// own layer has them; MarkupLayers passes it no ruler, Paste here or finger
// chords); onZoomStroke(box) hears of every stroke it commits, its
// strokeBounds. zoom: the zoom window when its box lies on this page, drawn
// over it (InkZoomRect), whose drags and a blank tap on any page call
// onZoom(page, {x, y} | {mag} | {at, size}) while a window is open.
export function InkLayer({ pageNumber, wrapRef, width, height, blocks, tool, penTool, penOnly, pressure, eraserMode,
  eraserSize = 1, eraserFilter = "all", drawHold = false, scribble = false, fingerUndo = "off", lassoMode = "free", selection, flash,
  clip = false, keys, hoverPreview = true, ruler = null, rulerUnits = "pt",
  onStroke, onEraseAlong, onEraseEnd, onEraseMany, onSelect, onAction, onMoveSelection, onJump, onPaste,
  onFingerScroll, onUndoGesture, onHoldStatus, onInkFeatures, onInkLive, onHaptic, onRuler,
  chrome = true, zoom = null, onZoom, onZoomStroke }) {
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
  // While a shape's handle is dragged: the stroke as the drag reshapes it, drawn in its place.
  const [reshape, setReshape] = useState(null);
  // The one-row "Paste here" menu: {x, y} on screen and `pt` on the page.
  const [pasteMenu, setPasteMenu] = useState(null);
  useLayoutEffect(() => { setTransform(null); setReshape(null); }, [selection]);
  const live = useRef({});
  // Whether a pen is writing on this layer, the page's own stroke or one
  // the native host took (the pointer effect sets it; the finger chords ask).
  const penBusy = useRef(() => false);
  // What a press in leaves the selection menu open: this page, and the zoom
  // window under the viewer, whose layer draws and drags the same selection.
  const menuIgnore = useRef(null);
  menuIgnore.current = { contains: (node) => !!(wrapRef.current?.contains(node) || node?.closest?.(".inkZoomPanel")) };

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
  // The ruler where it can be used: shown, moved and written along.
  const rulerOn = ruler && onRuler ? ruler : null;
  live.current = { tool, penTool, penOnly, pressure, eraserMode, eraserSize, eraserFilter, drawHold, scribble, fingerUndo, lassoMode,
    width, height, groups, selBox, replay, clip, hoverPreview, ruler: rulerOn, penBusy: () => penBusy.current(),
    onStroke, onEraseAlong, onEraseEnd, onEraseMany, onSelect, onMoveSelection, onJump, onPaste, onFingerScroll, onUndoGesture,
    onHoldStatus, onInkLive, onHaptic, onZoom, onZoomStroke };
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
    // inkBusy: anything this layer is drawing, or a stroke the host draws for it
    const layerBusy = () => drawing != null || hostOwned != null;
    busyLayers.add(layerBusy);
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
      // The Shapes tool's cursor is the crosshair alone; over the ruler's
      // band only a pen that writes there shows its footprint.
      if (use.tool === "shape" || (e.target.closest?.(".inkRulerBand") && !writesOnBand(L, e))) { hideCursor(); return; }
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
      // With Hover preview on, a pen's footprint follows its brush: a round
      // dot, a teardrop for a brush pen, and for a pencil an ellipse leaning
      // the way the pencil does when the pointer reports tilt, as wide as the
      // pencil's law draws at that slant (finishStroke.js), for a
      // calligraphy pen its nib — a bar its width long and a hairline thick
      // (NIB_HAIR, two pixels at least), turned by `--nib` to the angle the
      // next stroke would take, the hovering pen's twist applied as
      // strokeNib does —; a dashed or dotted preset's ring is dashed or
      // dotted. Off, a plain round ring.
      const look = !eraser && L.hoverPreview !== false;
      const brush = look && use.tool === "pen" ? brushValue(use.brush) : null;
      if (brush) cursor.dataset.brush = brush; else delete cursor.dataset.brush;
      const dash = look && (use.tool === "pen" || use.tool === "highlighter") && isDash(use.dash) ? use.dash : null;
      if (dash) cursor.dataset.dash = dash; else delete cursor.dataset.dash;
      const tilt = brush === "pencil" && e.pointerType === "pen" ? penTilt(e) : null;
      const nib = brush === "calligraphy" ? strokeNib(use.nib, e.pointerType === "pen" ? penTwist(e) : null, use.nibLock) : null;
      if (nib != null) cursor.style.setProperty("--nib", String(nib)); else cursor.style.removeProperty("--nib");
      const major = tilt ? use.size * k * widthLaw({ tool: "pen", brush: "pencil" })({ p: 0.5, a: tilt.a }) : diameter;
      const minor = tilt ? major * Math.max(0.4, Math.sin(tilt.a * Math.PI / 180)) : nib != null ? Math.max(2, diameter * NIB_HAIR) : diameter;
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
    // A draft that ends stops its frame, its prediction's expiry and its hold timer.
    const stopTimers = (d) => {
      if (d.raf) cancelAnimationFrame(d.raf);
      clearTimeout(d.predictionTimer);
      clearTimeout(d.holdTimer);
    };

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
    // A stabilised sample onto the draft, unless it repeats the last.
    const pushSample = (d, s) => {
      const previous = d.samples.at(-1);
      if (previous && s.x === previous.x && s.y === previous.y && s.p === previous.p) return;
      d.samples.push(s);
    };
    // A pointer sample into the draw context: through the preset's
    // stabilizer when it has one (the brush position comes back, or nothing
    // while the pointer is within the string), else as it is. `ending` is
    // the lift's pressure rule (inkInput.js); `lift` appends the
    // stabilizer's catch-up to the lift point. Along the ruler (`d.rail`)
    // every sample is projected onto the edge's line, p and t kept; such a
    // stroke has no smoothing.
    const takeSample = (d, ev, ending = false, lift = ending) => {
      if (!d.stab) {
        appendInkSample(d, ev, ending);
        if (d.rail && d.samples.length) d.samples[d.samples.length - 1] = onRail(d.samples.at(-1), d.rail);
        return;
      }
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
    // held line ends where the pen rests, not short of it. A pen's snap
    // ticks under the iPad host (onHaptic, at the tip on screen).
    const holdPath = (d, tip) => (d.stab && tip ? [...d.samples, { ...d.samples.at(-1), x: tip.x, y: tip.y }] : d.samples);
    const snapHold = (d, shift) => {
      const tip = d.tip || d.samples.at(-1);
      const hit = recognise(holdPath(d, d.tip), { k: d.k, kinds: d.kinds });
      if (!hit) return false;
      d.hold = { kind: hit.kind, params: hit.params, snap: hit.params, from: { x: tip.x, y: tip.y }, at: performance.now(),
        flash: 2, shift, preview: null };
      d.predicted = [];
      clearTimeout(d.predictionTimer);
      if (!d.raf) d.raf = requestAnimationFrame(paint);
      if (d.pen) live.current.onHaptic?.("snap", { x: d.rect.left + tip.x * d.k, y: d.rect.top + tip.y * d.k });
      return true;
    };
    // "Paste here" opens where the draft went down; the lift of its pointer
    // is not a click (pasteHold).
    const openPasteHere = (d) => {
      pasteHold = d.id;
      setPasteMenu({ x: d.downAt.x, y: d.downAt.y, pt: d.toPt({ clientX: d.downAt.x, clientY: d.downAt.y }) });
    };
    // A pen or mouse held still from its down, before it drew PASTE_MOVE css
    // px, with strokes on the clipboard: the draft goes (nothing commits)
    // and "Paste here" opens at the point. Fingers never arm it (a resting
    // palm must not paste); the click the lift would deliver is swallowed.
    const pasteHere = (d) => {
      if (drawing !== d) return;
      drawing = null;
      try { el.releasePointerCapture(d.id); } catch { /* already released */ }
      stopTimers(d);
      hideCanvas();
      liveEnd(d);
      openPasteHere(d);
    };
    const holdFire = (d) => {
      d.holdTimer = 0;
      if (drawing !== d || d.hold) return;
      // the paste hold: no travel, against the shape hold's size floor — a
      // tip that has travelled cannot paste, one that has not cannot snap
      if (d.pasteArmed && d.travel <= PASTE_MOVE) { pasteHere(d); return; }
      if (!d.holdOn || !holdQualifies(d.samples, d.k)) return;
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

    // The eraser's pass. `d.sweep` is the path it swept since the last
    // flush, from the point that flush ended at (the down point before the
    // first), so the flushes join into one path; every coalesced position
    // goes in. App gets it once an animation frame, at the lift and, for
    // what was swept already, at a cancel, as one onEraseAlong over every
    // group of the page (ink.js eraseAlong / hitStrokesAlong test the
    // capsules between the points, so a fast eraser leaves nothing it
    // crossed). The mode and the radius are the down's, and `pass`, minted
    // there, makes the gesture one undo entry.
    const sweepAdd = (d, events) => {
      for (const ev of events) {
        const p = d.toPt(ev), last = d.sweep[d.sweep.length - 1];
        if (p.x !== last.x || p.y !== last.y) d.sweep.push(p);
      }
    };
    const sweepFlush = (d) => {
      if (d.raf) { cancelAnimationFrame(d.raf); d.raf = 0; }
      if (d.swept && d.sweep.length < 2) return;
      const path = d.sweep;
      d.swept = true;
      d.sweep = [path[path.length - 1]];
      live.current.onEraseAlong?.(pageNumber, path, d.radius, { mode: d.eraseMode, pass: d.pass });
    };
    const sweepLater = (d) => {
      if (!d.raf) d.raf = requestAnimationFrame(() => { d.raf = 0; if (drawing === d) sweepFlush(d); });
    };

    // The live canvas covers what shows of the layer from the down
    // (showCanvas, canvasWindow: its window `d.cw`, css px of the layer at
    // the down's zoom) — over a page magnified past its viewer, the zoom
    // window's at 4× or a page at 400 %, what shows at the device's ratio,
    // not the whole page under canvasSize's caps. `viewOf` is the box the
    // layer is seen through, looked up once.
    let view;
    const viewOf = () => (view === undefined ? (view = viewportOf(el)) : view);
    // A frame reaching past the window (`box`, page units) grows it to take
    // in what shows of the layer now, when that is more (the view resized,
    // or scrolled, since the down); what lies outside the view is never
    // seen, so nothing grows for it. → whether it grew (the canvas is then
    // blank, its transform new).
    const growCanvas = (d, box) => {
      const w = d.cw, k = d.k;
      if (box[0] * k >= w.x && box[1] * k >= w.y && box[2] * k <= w.x + w.width && box[3] * k <= w.y + w.height) return false;
      const rect = el.getBoundingClientRect(), seen = canvasWindow(rect, viewRect(viewOf()));
      if (!seen || windowHolds(w, seen)) return false;
      d.cw = windowUnion(w, seen);
      fitCanvas(canvasRef.current, d.cw, rect, true);
      return true;
    };
    // The live canvas's context in page units. `dirty` (a box in page
    // units): clear only that part — the stroke's frames clear the union of
    // the last frame's box and this one's (dirty rects) — else all of it;
    // `reach`, the box the frame draws in (dirty's by default), may grow
    // the canvas (growCanvas).
    const canvasCtx = (d, dirty = null, reach = dirty) => {
      const canvas = canvasRef.current;
      if (!canvas || !d.cw) return null;
      const grown = !!reach && growCanvas(d, reach);
      const ctx = canvas.getContext("2d", { desynchronized: true });
      if (!ctx) return null;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (!dirty || grown) ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(...windowMatrix(canvas, d.cw, d.k));
      if (dirty && !grown) ctx.clearRect(dirty[0], dirty[1], dirty[2] - dirty[0], dirty[3] - dirty[1]);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      ctx.setLineDash([]);
      return ctx;
    };
    const paint = () => {
      const d = drawing;
      if (!d) return;
      d.raf = 0;
      if (d.mode === "shape") { paintShape(d); return; }
      if (d.mode !== "lasso") { paintStroke(d); return; }
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of d.poly) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
      const ctx = canvasCtx(d, null, [x0, y0, x1, y1]);
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
      // Along the ruler: the line between the farthest projections so far,
      // at the preset's constant width, as it commits.
      const rail = !hold && d.rail && d.samples.length ? railLine(d.samples, d.rail) : null;
      const even = hold || rail;
      const samples = hold ? hold.preview : rail ? shapeSamples("line", rail, { span: 0, p: 0.5 }) : [...d.samples, ...(d.predicted || [])];
      // Every width a brush draws stays within two sizes of its centre line.
      const pad = 2 * (use.size || 1) + 2 / d.k;
      let box = samplesBox(samples, pad);
      if (!hold && d.level === 4 && d.tip) box = unionBox(box, [d.tip.x - pad, d.tip.y - pad, d.tip.x + pad, d.tip.y + pad]);
      const ctx = canvasCtx(d, unionBox(d.lastBox, box) || [0, 0, 0, 0]);
      d.lastBox = box;
      if (!ctx) return;
      // A held shape draws as it commits (finishStroke's hold stage): pen
      // false and a shape, so constant width whatever the brush — no law.
      paintLiveStroke(ctx, samples, even ? { ...d.look, pen: false, shape: hold ? hold.kind : "line" } : d.look,
        { color: flash ? SNAP_BLUE : use.color, flash, done: !!even });
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
    // A frame of the Shapes tool's drag: the shape from the down point to
    // the pointer (shapeTool.js toolShape, Shift as the pointer reports it)
    // as it commits — the stylus pen's look at its constant width, over its
    // light fill for a closed shape while Fill is on (shapeFill). Nothing
    // under the floor.
    const shapeFill = (use, kind) => (use.fill && CLOSED_SHAPES.has(kind) ? { fill: { color: use.style.color } } : null);
    const paintShape = (d) => {
      const { kind, style } = d.use;
      const shape = toolShape(kind, d.from, d.to, { shift: d.shift, k: d.k });
      const samples = shape ? shapeSamples(shape.kind, shape.params, { span: 0, p: 0.5 }) : [];
      const box = samplesBox(samples, 2 * (style.size || 1) + 2 / d.k);
      const ctx = canvasCtx(d, unionBox(d.lastBox, box) || [0, 0, 0, 0]);
      d.lastBox = box;
      if (!ctx || !samples.length) return;
      paintLiveStroke(ctx, samples, { ...style, pen: false, shape: shape.kind, ...shapeFill(d.use, shape.kind) }, { done: true });
    };
    // At the down: the canvas on what shows of the layer (the window the
    // draw context carries, `cw`), blank.
    const showCanvas = (ctx) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      ctx.cw = canvasWindow(ctx.rect, viewRect(viewOf())) || { x: 0, y: 0, width: ctx.rect.width, height: ctx.rect.height };
      fitCanvas(canvas, ctx.cw, ctx.rect, true);
    };
    const hideCanvas = () => { if (canvasRef.current) releaseCanvas(canvasRef.current); };

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
      d.live = { sent: 0, timer: 0, last: -Infinity, style: inkLive.liveStyle(d.look, d.look.pen) };
      liveTick(d);
    };
    const liveChunk = (d, done) => {
      const lv = d.live, samples = inkLive.liveSamples(d.samples, lv.sent, lv.style.pen);
      const msg = { id: d.liveId, style: lv.style, n: lv.sent, samples };
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

    // A stroke ready to save: to the page's ink under its history label
    // (onStroke), and its box to the zoom window (onZoomStroke).
    const saveStroke = (stroke, label) => {
      const L = live.current;
      L.onStroke?.(pageNumber, stroke, { width: L.width, height: L.height }, { label });
      L.onZoomStroke?.(strokeBounds(stroke));
    };
    // A finished stroke's samples (page units) through finishStroke (the
    // held shape, a straight highlight, a scribble that erases, else
    // decimated with the widths baked) to the page's ink.
    const commitStroke = (d) => {
      if (!d.samples.length) return false;
      // the preset's look, a stylus with the pressure preference off writing
      // like a mouse (pen false: an even line, the Brush's speed law)
      const L = live.current, { use } = d, pen = d.look.pen;
      const style = { ...encodedStyle(use), pen, nib: d.nib };
      // a stroke along the ruler commits through the hold stage as its line (no pill)
      const rail = d.rail ? railLine(d.samples, d.rail) : null;
      const result = finishStroke(d.samples, { style, pen, k: d.k,
        hold: rail ? { kind: "line", params: rail } : d.hold ? { kind: d.hold.kind, params: d.hold.params } : null, straight: use.straight,
        scribble: !!L.scribble, groups: L.groups, keep: eraserKeep(L.eraserFilter) });
      const { commit, erase, status } = result;
      const look = result.style || style;
      if (erase?.length) L.onEraseMany?.(pageNumber, erase);
      if (status && !rail) L.onHoldStatus?.(status);
      if (!commit?.length) return !!erase?.length;
      const stroke = encodeStroke({ ...look, id: d.liveId, t0: d.t0, samples: commit, ch: strokeChannels(look, commit) });
      d.liveCommitted = true;
      saveStroke(stroke, result.label);
      return true;
    };
    // The Shapes tool's lift: the shape of the drag (nothing under the
    // floor) through finishStroke's hold stage — constant width, xyt, t
    // spread over the drag, t0 the down's — with its fill while Fill is on
    // (a rectangle or an ellipse), to the page's ink as any stroke.
    const commitShape = (d, e) => {
      const { kind, style } = d.use, to = d.toPt(e);
      const shape = toolShape(kind, d.from, to, { shift: !!e.shiftKey, k: d.k });
      if (!shape) return false;
      const span = Math.max(0, Math.round(e.timeStamp - d.startTime));
      const look = { ...encodedStyle(style), ...shapeFill(d.use, shape.kind) };
      const result = finishStroke([{ ...d.from, p: 0.5, t: 0 }, { ...to, p: 0.5, t: span }], { style: look, pen: false, k: d.k, hold: shape });
      if (!result.commit?.length) return false;
      const stroke = encodeStroke({ ...result.style, t0: d.t0, samples: result.commit, ch: strokeChannels(result.style, result.commit) });
      saveStroke(stroke, result.label);
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
      stopTimers(d);
      hideCanvas();
      liveEnd(d);
    };
    // The host's raw samples ([x, y, p, t] or [x, y, p, t, a, z], viewport
    // css px and ms from the first) as the pointer path samples its own,
    // with the rect and zoom of the down (hostInk.js hostEvent: the tilt
    // back to radians): a draw context of their own, through the preset's
    // stabilizer, the lift's catch-up at the last sample when `lift`.
    const hostDraft = (d, samples, lift) => {
      const c = { ...d, samples: [], startTime: 0, hold: null, tip: null, stab: d.level ? makeStabilizer(d.level, { k: d.k }) : null };
      samples.forEach((s, i) => takeSample(c, hostEvent(s), false, lift && i === samples.length - 1));
      return c;
    };
    // A pen stroke under a host that captures the Pencil: claim it (with
    // where it went down, which tells the host which touch it is, and the
    // tool in the preset's look, hostInk.js claimTool) and keep drawing the
    // draft until the host answers. Taken, the draft goes and the host's
    // samples commit instead; left, the stroke ends here.
    const claimForHost = (d, e) => {
      const at = { left: d.rect.left, top: d.rect.top, k: d.k };
      const claim = {
        taken: false,
        // The shape a hold snapped to, as the host was told it: {kind,
        // params, from, n, tSnap} in css px (docs/dev/ipad.md "The pen").
        held: null,
        // The host's Pencil has stayed still (its `hold`, the samples so
        // far): with strokes on the clipboard and the pen never 2 css px
        // from its first sample, the claim ends here and Paste here opens
        // where it went down ({cancel}: the host drops its line); with draw
        // and hold on, the stroke's extent at the size floor (shapes.js
        // holdQualifies, over the samples in page points) and the stroke
        // HOLD_MIN_MS old at the stillness anchor, the recogniser runs as
        // the layer's own hold does, and a shape is kept on the claim and
        // answered in css px for the host to draw and refit. Else nothing;
        // a claim keeps one shape (later holds answer nothing).
        hold: (samples) => {
          // along the ruler nothing is recognised, nor pasted
          if (!claim.taken || claim.held || d.rail || hostClaims.get(d.id) !== claim || !samples.length) return {};
          const pts = samples.map(([x, y, , t]) => ({ x, y, t }));
          const first = pts[0], last = pts[pts.length - 1];
          if (d.pasteArmed && pts.every((q) => Math.hypot(q.x - first.x, q.y - first.y) <= PASTE_MOVE)) {
            hostClaims.delete(d.id);
            if (hostOwned === d.id) hostOwned = null;
            openPasteHere(d);
            return { cancel: true };
          }
          const still = stillSince(pts);
          if (!d.holdOn || !holdQualifies(pts.map((q) => d.toPt({ clientX: q.x, clientY: q.y })), d.k)
            || pts[still].t < HOLD_MIN_MS) return {};
          const c = hostDraft(d, samples, false);
          const hit = recognise(holdPath(c, d.toPt({ clientX: last.x, clientY: last.y })), { k: d.k, kinds: d.kinds });
          const shape = hit && shapeToHost(hit, at);
          if (!shape) return {};
          claim.held = { kind: shape.kind, params: shape.params, from: { x: last.x, y: last.y }, n: samples.length, tSnap: pts[still].t + HOLD_MS };
          live.current.onHaptic?.("snap", { x: last.x, y: last.y });
          return { shape: { kind: shape.kind, params: shape.params } };
        },
        // The lift: the host's samples commit as the layer's own would. A
        // shape the claim kept, when the host drew it (`held`, the `n` of
        // its hold: a lift that came before the host had the answer drew
        // the freehand line, and names none), goes through the samples
        // after the hold in the host's own space (shapes.js followHold, the
        // rule the shell drew by), to page points for finishStroke's hold
        // stage; dropped there, the stroke is freehand.
        commit: (samples, held) => {
          handOver(d);
          if (hostOwned === d.id) hostOwned = null;
          const c = hostDraft(d, samples, true);
          const kept = claim.held && held === claim.held.n ? claim.held : null;
          const params = kept && samples.length ? followHold(kept, samples.map(([x, y, , t]) => ({ x, y, t }))) : null;
          const shape = params && shapeFromHost({ kind: kept.kind, params }, at);
          if (shape) c.hold = { kind: shape.kind, params: shape.params };
          // a calligraphy nib turned by the roll the shell's first sample carries (its seventh value)
          if (d.nib != null) c.nib = strokeNib(d.use.nib, samples.length ? penTwist(hostEvent(samples[0])) : null, d.use.nibLock);
          return commitStroke(c);
        },
      };
      hostClaims.set(d.id, claim);
      const drop = () => { if (hostClaims.get(d.id) === claim) hostClaims.delete(d.id); };
      // a calligraphy pen claims the preset's nib: the shell turns it by the Pencil's own roll (nibRoll)
      const tool = claimTool({ ...d.look, ...(d.nib != null ? { nib: d.use.nib } : {}) }, { k: d.k });
      // Along the ruler the claim carries `snap`, the edge for the shell's live line (claim.commit projects either way).
      if (d.rail) tool.snap = hostSnap(d.rail, d.railAngle, at);
      postToHost({ type: "claim", pointerId: d.id, at: { x: e.clientX, y: e.clientY }, tool })
        .then((reply) => {
          if (hostClaims.get(d.id) !== claim) return; // the page already ended it
          if (!reply?.taken) { drop(); return; }
          claim.taken = true;
          handOver(d);
        }, drop);
    };

    const onDown = (e) => {
      // the touch whose hold opened Paste here is over once its pointer goes down again
      if (e.pointerId === pasteHold) pasteHold = null;
      if (e.target.closest?.(NOT_INK)) { hideCursor(); return; }
      // The ruler's band: a pen about to write writes along it (below); any
      // other press is the band's own, which moves the ruler (InkRuler) —
      // but a touch while a pen writes stays a palm (below), resting on the
      // ruler as a hand does.
      const band = !!e.target.closest?.(".inkRulerBand");
      const palm = e.pointerType !== "pen" && (hostOwned != null || drawing?.pointerType === "pen");
      if (band && !palm && !writesOnBand(live.current, e)) { hideCursor(); return; }
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
        drawing = { ...ctx, id: e.pointerId, mode: "erase", pass: strokeId(), raf: 0, sweep: [ctx.toPt(e)], swept: false,
          eraseMode: L.eraserMode === "partial" ? "partial" : "stroke", radius: (ERASER_SIZES[L.eraserSize] ?? ERASER_SIZES[1]) / ctx.k };
        sweepLater(drawing);
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
      // The Shapes tool: a drag from the down point (paintShape, commitShape).
      // No hold, no Paste here, no scribble, no wet stroke, no claim.
      if (ctx.use.tool === "shape") {
        showCanvas(ctx);
        const pt = ctx.toPt(e);
        drawing = { ...ctx, id: e.pointerId, mode: "shape", from: pt, to: pt, shift: !!e.shiftKey, raf: 0, lastBox: null,
          t0: Date.now(), startTime: e.timeStamp };
        return;
      }
      showCanvas(ctx);
      const pen = e.pointerType === "pen";
      // The ruler: a stroke begun on its band (a pen) or within its reach
      // outside an edge runs along that edge (`rail`; ruler.js snapRail).
      const rail = L.ruler ? snapRail(L.ruler, ctx.toPt(e), ctx.k, { band }) : null;
      const level = rail ? 0 : ctx.use.stabilize || 0;
      // a calligraphy pen's nib: the preset's angle turned by the pen's twist
      // at this, its first sample, unless the preset locks it (strokeNib)
      const nib = ctx.use.tool === "pen" && ctx.use.brush === "calligraphy"
        ? strokeNib(ctx.use.nib, penTwist(e), ctx.use.nibLock) : undefined;
      drawing = { ...ctx, id: e.pointerId, mode: "stroke", samples: [], t0: Date.now(),
        startTime: e.timeStamp, pressure: live.current.pressure, pen, raf: 0, nib,
        // the look the live canvas draws and the stroke commits with: a
        // stylus with the pressure preference off has no pressure to read
        look: { ...ctx.use, pen: pen && !!live.current.pressure, ...(nib != null ? { nib } : {}) },
        // smoothing (stabilizer.js) and draw-and-hold (shapes.js), both
        // snapshotted at the down like the pressure preference
        level, stab: level ? makeStabilizer(level, { k: ctx.k }) : null, tip: null,
        rail, railAngle: rail ? L.ruler.angle : null,
        hold: null, holdTimer: 0, holdOn: !rail && !!L.drawHold && !(ctx.use.tool === "highlighter" && ctx.use.straight === "off"),
        kinds: ctx.use.tool === "highlighter" ? ["line"] : undefined,
        tail: { x: e.clientX, y: e.clientY, at: e.timeStamp }, downAt: { x: e.clientX, y: e.clientY },
        // the paste hold (pasteHere): a pen or mouse, strokes on the clipboard
        pasteArmed: !rail && !!L.clip && !!L.onPaste && e.pointerType !== "touch", travel: 0,
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
      if (d.mode === "erase") {
        sweepAdd(d, coalesced(e));
        sweepLater(d);
        return;
      }
      if (d.mode === "move") {
        const pt = d.toPt(e);
        d.dx = pt.x - d.start.x;
        d.dy = pt.y - d.start.y;
        if (!d.raf) d.raf = requestAnimationFrame(() => { d.raf = 0; setDragOffset({ dx: d.dx, dy: d.dy }); });
        return;
      }
      if (d.mode === "shape") {
        d.to = d.toPt(e);
        d.shift = !!e.shiftKey;
        if (!d.raf) d.raf = requestAnimationFrame(paint);
        return;
      }
      for (const ev of coalesced(e)) {
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
        d.predicted = d.hold || d.stab || d.rail ? [] : predictedInkSamples(d, e);
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
          if (!p.hit && !L.selBox && L.onZoom && L.width && !e.target?.closest?.(ABOVE_PASSIVE_INK)) {
            const rect = el.getBoundingClientRect(), k = rect.width / L.width;
            zoomAt({ x: (p.x - rect.left) / k, y: (p.y - rect.top) / k });
          }
          if (p.hit || L.selBox) { e.preventDefault(); e.stopPropagation(); swallowClick(); }
        }
        return;
      }
      const d = drawing;
      if (!d || e.pointerId !== d.id) return;
      drawing = null;
      try { el.releasePointerCapture(e.pointerId); } catch { /* already released */ }
      stopTimers(d);
      const L = live.current;
      if (d.mode === "erase") {
        // the rest of the pass: to the lift's own point, or at a cancel as
        // far as it had gone
        if (!cancelled) sweepAdd(d, [e]);
        sweepFlush(d);
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
      if (d.mode === "shape") { commitShape(d, e); return; }
      if (d.mode === "lasso") {
        const items = [];
        if (d.poly.every(([x, y]) => Math.hypot(x - d.start.x, y - d.start.y) * d.k <= 8)) {
          const hit = nearestInkStroke(L.groups, d.start.x, d.start.y, 10 / d.k);
          if (hit) items.push(hit);
          else if (!L.selBox) zoomAt(d.start);
        } else if (d.poly.length >= 3) {
          for (const g of L.groups) {
            const ids = strokesInLasso(g.ink, d.poly);
            if (ids.length) items.push({ id: g.id, ids });
          }
        }
        L.onSelect?.(pageNumber, items);
        if (items.length && d.pointerType === "pen") L.onHaptic?.("select", { x: e.clientX, y: e.clientY });
        return;
      }
      if (!d.samples.length) { liveEnd(d); return; }
      if (d.deselect) { d.deselect = false; L.onSelect?.(pageNumber, []); }
      takeSample(d, e, true);
      commitStroke(d);
      liveEnd(d);
    };
    // While a zoom window is open (onZoom), a tap or a lasso's click on a
    // blank spot of this page centres the window there (one that only
    // dismisses a selection does not); never the panel's own layer, which
    // gets no onZoom.
    const zoomAt = (pt) => {
      const L = live.current;
      if (L.onZoom && L.width) L.onZoom(pageNumber, { at: { x: pt.x, y: pt.y }, size: { width: L.width, height: L.height } });
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
      // a Pencil on a comment card scrolls and presses it, as a finger does
      if (!drawing && e.target.closest?.(BESIDE_PAGE)) return;
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
      if (e.target.closest?.(BESIDE_PAGE)) return; // a comment card keeps its own menu
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
      busyLayers.delete(layerBusy);
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
      if (drawing) {
        stopTimers(drawing);
        try { el.releasePointerCapture(drawing.id); } catch { /* already released */ }
        liveEnd(drawing);
      }
      hideCanvas();
    };
  }, [wrapRef, pageNumber]);

  useEffect(() => { refreshCursorRef.current?.(); }, [tool, penTool, eraserSize, eraserFilter, hoverPreview]);

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
  const selectedStrokes = selectedIds.size ? groups.flatMap((g) => g.ink.strokes.filter((s) => selectedIds.has(s.id))) : [];
  // Editable shapes: a selection of exactly one stroke whose shape record
  // has handles (shapes.js shapeHandles) shows them, and a drag of one draws
  // the stroke reshaped (`reshape`) in its place until the release commits.
  const lone = selectedIds.size === 1 ? selectedStrokes : [];
  const shaped = lone.length === 1 && lone[0].shape && shapeHandles(lone[0].shape.kind, lone[0].shape.params).length ? lone[0] : null;
  const reshaped = reshape && shaped && reshape.id === shaped.id ? reshape : null;
  return (
    <GrainContext.Provider value={grain.fill}>
      <svg ref={svgRef} className="inkLayer" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        {grain.defs}
        {/* every group's band-0 strokes (highlighters) under every group's ink; on a dense
            page a group off the view is left empty and a finished one drawn as its picture */}
        {[0, 1].map((band) => groups.map((g) => {
          if (!g.ink.strokes.some((s) => strokeBand(s) === band)) return null;
          const culled = shown && !shown.has(g.id);
          const pic = culled ? null : raster(`${g.id}:${band}`, g.ink);
          return (
            <g key={`${g.id}:${band}`} data-ink-id={g.id} data-ink-band={band}
              style={{ pointerEvents: armed || !onSelect ? "none" : "visiblePainted", cursor: "pointer" }}>
              {culled ? null : pic ? (
                // the picture takes no presses: an image is hit over its whole box, the paths
                // on their ink only, and a tap selects from the samples (nearestInkStroke)
                <image className="inkRaster" href={pic.url} x={pic.box[0]} y={pic.box[1]} width={pic.box[2]} height={pic.box[3]}
                  preserveAspectRatio="none" style={band === 0 ? { mixBlendMode: "multiply", pointerEvents: "none" } : { pointerEvents: "none" }} />
              ) : (
                <ReplayStrokes ink={g.ink} band={band} frame={frame && groupInScope(replay, g.id, g.ink, pageNumber) ? frame : null}
                  hide={dragging || reshaped ? selectedIds : null} lazy={lazy.has(g.id)} />
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
        {reshaped && !dragging ? <g className="inkReshape"><Strokes ink={{ strokes: [reshaped] }} /></g> : null}
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
        style={{ left: pct(hitBox[0], width), top: pct(hitBox[1], height),
          width: pct(hitBox[2] - hitBox[0], width), height: pct(hitBox[3] - hitBox[1], height) }} /> : null}
      {selBox && onAction && chrome && !dragging ? <InkSelectionMenu wrapRef={wrapRef} ignoreRef={menuIgnore} box={selBox} width={width}
        strokes={selectedStrokes}
        canMerge={canMergeSelection(groups, selectedIds)} keys={keys}
        onAction={onAction} onClose={() => onSelect(pageNumber, [])} /> : null}
      {pasteMenu && onPaste ? (
        <ContextMenu x={pasteMenu.x} y={pasteMenu.y} className="inkPasteMenu" onClose={() => setPasteMenu(null)}>
          <MenuItem icon={ClipboardIcon} onClick={() => { setPasteMenu(null); onPaste(pageNumber, pasteMenu.pt, { width, height }); }}>
            {t("Paste here")}
          </MenuItem>
        </ContextMenu>
      ) : null}
      {selBox && onAction && chrome ? <InkTransformHandles wrapRef={wrapRef} box={selBox} width={width} height={height}
        positions={handlePositions}
        maxScale={Math.min(10, MAX_STROKE_SIZE / Math.max(...selectedStrokes.map(strokeMaxWidth)))}
        onPreview={setTransform} onCommit={(value) => onAction("transform", value)}
        shape={shaped} place={previewPoint} onShapePreview={setReshape}
        onShapeCommit={(params) => onAction("reshape", { id: shaped.id, params })} /> : null}
      {rulerOn ? <InkRuler wrapRef={wrapRef} ruler={rulerOn} width={width} height={height} units={rulerUnits} onRuler={onRuler} /> : null}
      {zoom && onZoom && chrome ? <InkZoomRect wrapRef={wrapRef} page={pageNumber} zoom={zoom} width={width} height={height} onZoom={onZoom} /> : null}
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

// A length as a percentage of `of`: the controls laid over a page and its
// canvases are placed in the page's percentages, so they follow it through
// the viewer's zoom. A focused control's arrow keys: the step each one
// moves (ARROWS, y down) and its sign (arrowSign: right and up are 1, left
// and down -1).
const pct = (v, of) => `${(v / of) * 100}%`;
const ARROWS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
const arrowSign = (key) => (key === "ArrowRight" || key === "ArrowUp" ? 1 : key === "ArrowLeft" || key === "ArrowDown" ? -1 : 0);

// The selection's resize and rotate buttons, and, for a shape held alone
// in the selection (`shape`, a stroke with a shape record), its own
// handles (InkShapeHandles).
function InkTransformHandles({ wrapRef, box, width, height, positions, maxScale, onPreview, onCommit, shape, place,
  onShapePreview, onShapeCommit }) {
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
      style={{ left: pct(positions[mode][0], width), top: pct(positions[mode][1], height) }}
      onPointerDown={(e) => begin(e, mode)} onPointerMove={move} onPointerUp={finish}
      onPointerCancel={cancel} onLostPointerCapture={cancel} onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        const sign = arrowSign(e.key);
        if (!sign) return;
        e.preventDefault(); e.stopPropagation();
        onCommit({ cx: (box[0] + box[2]) / 2, cy: (box[1] + box[3]) / 2,
          scale: mode === "resize" ? Math.min(maxScale, sign > 0 ? 1.1 : 1 / 1.1) : 1,
          angle: mode === "rotate" ? sign * Math.PI / 12 : 0 });
      }}>{mode === "resize" ? <ResizeIcon /> : <RedoIcon />}</button>)}
    {shape ? <InkShapeHandles wrapRef={wrapRef} stroke={shape} width={width} height={height}
      place={place} onPreview={onShapePreview} onCommit={onShapeCommit} /> : null}
  </>;
}

// A shape's handles (docs/dev/handwriting.md "Editable shapes"): a 28 px
// button at each of its record's handles (shapes.js shapeHandles), placed
// through `place` (the selection's live preview); a drag previews the
// stroke reshaped (dragHandle, reshapeStroke: `onPreview(stroke | null)`)
// and the release commits its params (`onCommit(params)`, one history
// entry), as does each press of an arrow key on a focused handle (1 pt,
// Shift 10 pt). Shift while dragging a line's or an arrow's end turns it in
// 15° steps.
const handleName = (kind, id, i) => {
  if (id === "p1") return kind === "arrow" ? t("Arrow tail") : t("Line start");
  if (id === "p2") return kind === "arrow" ? t("Arrow tip") : t("Line end");
  if (id === "rx") return t("Ellipse length");
  if (id === "ry") return t("Ellipse width");
  return t("Corner {n}", { n: i + 1 });
};
function InkShapeHandles({ wrapRef, stroke, width, height, place, onPreview, onCommit }) {
  const { kind, params } = stroke.shape;
  const [live, setLive] = useState(null);   // the params while a handle is dragged
  const drag = useRef(null);
  const previewRef = useRef(onPreview);
  previewRef.current = onPreview;
  const cancel = () => { drag.current = null; setLive(null); previewRef.current(null); };
  useEffect(() => {
    const escape = (e) => { if (e.key === "Escape" && drag.current) cancel(); };
    window.addEventListener("blur", cancel);
    window.addEventListener("keydown", escape);
    return () => { drag.current = null; window.removeEventListener("blur", cancel); window.removeEventListener("keydown", escape); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const pointAt = (e) => {
    const rect = wrapRef.current.getBoundingClientRect(), k = rect.width / width;
    return { x: (e.clientX - rect.left) / k, y: (e.clientY - rect.top) / k };
  };
  const begin = (e, id) => {
    if (e.button !== 0 || drag.current) return;
    e.preventDefault(); e.stopPropagation();
    drag.current = { pointer: e.pointerId, id, params: null };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    const d = drag.current;
    if (d?.pointer !== e.pointerId) return;
    e.preventDefault(); e.stopPropagation();
    d.params = dragHandle(kind, params, d.id, pointAt(e), { shift: e.shiftKey });
    setLive(d.params);
    previewRef.current(reshapeStroke(stroke, d.params));
  };
  const finish = (e) => {
    const d = drag.current;
    if (d?.pointer !== e.pointerId) return;
    e.preventDefault(); e.stopPropagation();
    cancel();
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    if (d.params && JSON.stringify(d.params) !== JSON.stringify(params)) onCommit(d.params);
  };
  return shapeHandles(kind, live || params).map((h, i) => {
    const [x, y] = place(h.x, h.y);
    return (
      <button key={h.id} type="button" className="inkTransformHandle inkShapeHandle" data-handle={h.id}
        aria-label={handleName(kind, h.id, i)} title={t("Drag to reshape; arrow keys move it")}
        style={{ left: pct(x, width), top: pct(y, height) }}
        onPointerDown={(e) => begin(e, h.id)} onPointerMove={move} onPointerUp={finish}
        onPointerCancel={cancel} onLostPointerCapture={(e) => { if (drag.current?.pointer === e.pointerId) cancel(); }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          const step = ARROWS[e.key];
          if (!step || drag.current) return;
          e.preventDefault(); e.stopPropagation();
          const by = e.shiftKey ? 10 : 1;
          const next = dragHandle(kind, params, h.id, { x: h.x + step[0] * by, y: h.y + step[1] * by });
          if (JSON.stringify(next) !== JSON.stringify(params)) onCommit(next);
        }}><span className="inkShapeDot" aria-hidden="true" /></button>
    );
  });
}

// The ruler over its surface (docs/dev/handwriting.md "Client", the ruler),
// in the selection blue: an anchor on its middle line in page percentages,
// as long as the ruler and turned by its angle — the unscaled wrapper its
// controls sit in, so the band keeps RULER_PX css px at any zoom — holding
// the band (touch-action none, its touches its own: a finger or the mouse
// drags it, a second finger anywhere turns it with the first; its ticks and
// both edges an SVG in css px), the knob in the middle with the angle (a
// drag turns the ruler, Shift or the ruler's snap in 15° steps; a tap snaps
// it; a double tap turns it 90°) and a handle at either end (a drag
// lengthens or shortens it, the other end staying). A gesture previews here
// and hands its result to onRuler at its end; a focused band, knob or end
// takes the arrow keys, each press one change. A pen with a stroke tool
// never reaches the band: the layer takes it to write along an edge.
const TICK_PX = [5, 8, 12];
function InkRuler({ wrapRef, ruler, width, height, units, onRuler }) {
  const [preview, setPreview] = useState(null);
  const previewRef = useRef(null);
  const gesture = useRef(null);   // {kind: "move" | "turn" | "end", id, start, from, moved, touch, pts, ref, end, pair}
  const bandRef = useRef(null);
  const latest = useRef(null);
  latest.current = { ruler, onRuler, width };
  // css px per pt, for the ticks' density
  const [k, setK] = useState(1);
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el || !width) return undefined;
    const measure = () => { const w = el.getBoundingClientRect().width; if (w) setK(w / width); };
    measure();
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, [wrapRef, width]);
  const show = (next) => { previewRef.current = next; setPreview(next); };
  const toPt = (e) => {
    const r = wrapRef.current.getBoundingClientRect(), kk = r.width / latest.current.width;
    return { x: (e.clientX - r.left) / kk, y: (e.clientY - r.top) / kk, k: kk };
  };
  // While a finger holds the band, no touch reaches the viewer's pan and
  // pinch nor the page's own handlers: window capture listeners, added
  // when that finger goes down and taken away when its gesture ends — a
  // non-passive touch listener left on the window would hold every scroll
  // in the app on the main thread for as long as the ruler shows.
  const touchHold = useRef(null);
  const holdTouches = (on) => {
    if (on === !!touchHold.current) return;
    if (!on) { touchHold.current(); touchHold.current = null; return; }
    const touch = (e) => {
      e.stopPropagation();
      if (e.cancelable && e.type === "touchmove") e.preventDefault();
    };
    window.addEventListener("touchstart", touch, { capture: true, passive: false });
    window.addEventListener("touchmove", touch, { capture: true, passive: false });
    touchHold.current = () => {
      window.removeEventListener("touchstart", touch, { capture: true });
      window.removeEventListener("touchmove", touch, { capture: true });
    };
  };
  const end = (commit) => {
    const next = previewRef.current;
    gesture.current = null;
    holdTouches(false);
    show(null);
    if (commit && next) latest.current.onRuler?.(next);
  };
  // While a finger holds the band: a second touch anywhere joins it and the
  // two turn and carry the ruler (ruler.js pinchRuler).
  useEffect(() => {
    const down = (e) => {
      const g = gesture.current;
      // a pen going down while a finger holds the band: that finger was a
      // palm, and the ruler goes back to where it lay
      if (g?.touch && e.pointerType === "pen") { end(false); return; }
      if (!g?.touch || e.pointerType !== "touch" || e.pointerId === g.id) return;
      e.preventDefault();
      e.stopPropagation();
      if (g.pair) return;   // a third finger does nothing
      const p = toPt(e);
      g.pts.set(e.pointerId, p);
      g.pair = { id: e.pointerId, start: previewRef.current || g.start, from: [g.pts.get(g.id), p] };
    };
    const move = (e) => {
      const g = gesture.current;
      if (!g?.pair || (e.pointerId !== g.id && e.pointerId !== g.pair.id)) return;
      e.stopPropagation();
      g.pts.set(e.pointerId, toPt(e));
      show(pinchRuler(g.pair.start, g.pair.from, [g.pts.get(g.id), g.pts.get(g.pair.id)], { snap: !!g.start.snap }));
    };
    const up = (e) => {
      const g = gesture.current;
      if (!g?.pair || e.pointerId !== g.pair.id) return;
      e.stopPropagation();
      // one finger again: it moves the ruler on from where the two left it
      g.pts.delete(e.pointerId);
      g.start = previewRef.current || g.start;
      g.from = g.pts.get(g.id);
      g.pair = null;
    };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", up, true);
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", up, true);
      holdTouches(false);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // The band's own touches stop at the band (a native listener: React's run
  // at the root, after the viewer's), so its pinch never sees two fingers on it.
  useEffect(() => {
    const band = bandRef.current;
    if (!band) return undefined;
    const stop = (e) => { e.stopPropagation(); if (e.cancelable && e.type === "touchmove") e.preventDefault(); };
    band.addEventListener("touchstart", stop, { passive: false });
    band.addEventListener("touchmove", stop, { passive: false });
    return () => { band.removeEventListener("touchstart", stop); band.removeEventListener("touchmove", stop); };
  }, []);
  const begin = (e, kind, extra = null) => {
    if (gesture.current || (e.pointerType === "mouse" && e.button !== 0)) return;
    e.preventDefault();
    e.stopPropagation();
    const p = toPt(e);
    gesture.current = { kind, id: e.pointerId, start: ruler, from: p, moved: false, touch: e.pointerType === "touch",
      pts: new Map([[e.pointerId, p]]), ref: null, pair: null, ...extra };
    if (gesture.current.touch) holdTouches(true);
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* capture is a nicety */ }
  };
  const move = (e) => {
    const g = gesture.current;
    if (!g || g.id !== e.pointerId) return;
    e.preventDefault();
    e.stopPropagation();
    if (g.pair) return;   // the two fingers move it (above)
    const p = toPt(e);
    g.pts.set(e.pointerId, p);
    if (!g.moved && Math.hypot(p.x - g.from.x, p.y - g.from.y) * p.k < 3) return;
    g.moved = true;
    if (g.kind === "move") show(moveRuler(g.start, p.x - g.from.x, p.y - g.from.y));
    else if (g.kind === "end") show(stretchRuler(g.start, g.end, p));
    else {
      // the knob: the ruler turns as the pointer goes round its middle,
      // from where the pointer first stood 12 px out
      const dx = (p.x - g.start.x) * p.k, dy = (p.y - g.start.y) * p.k;
      if (Math.hypot(dx, dy) < 12) return;
      const at = (Math.atan2(-dy, dx) * 180) / Math.PI;
      if (g.ref == null) { g.ref = at; return; }
      show(turnRuler(g.start, at - g.ref, { snap: e.shiftKey || !!g.start.snap }));
    }
  };
  const up = (e) => {
    const g = gesture.current;
    if (!g || g.id !== e.pointerId) return;
    e.preventDefault();
    e.stopPropagation();
    if (g.kind === "turn" && !g.moved) {
      // a tap on the knob: the nearest 15°
      end(false);
      const r = latest.current.ruler, angle = snapAngle(r.angle);
      if (angle !== r.angle) latest.current.onRuler?.({ ...r, angle });
      return;
    }
    end(true);
  };
  const cancel = (e) => { if (gesture.current?.id === e.pointerId) end(false); };
  // a double tap (or double-click) on the knob turns the ruler a quarter turn
  const twice = doublePress(() => { const r = latest.current.ruler; latest.current.onRuler?.(turnRuler(r, 90)); });
  const bandKey = (e) => {
    const a = ARROWS[e.key];
    if (!a) return;
    e.preventDefault();
    e.stopPropagation();
    const step = e.shiftKey ? 10 : 1;
    onRuler(moveRuler(ruler, a[0] * step, a[1] * step));
  };
  const knobKey = (e) => {
    const sign = arrowSign(e.key);
    if (!sign) return;
    e.preventDefault();
    e.stopPropagation();
    onRuler({ ...ruler, angle: e.shiftKey || ruler.snap ? stepAngle(ruler.angle, sign) : turnRuler(ruler, sign).angle });
  };
  const endKey = (which) => (e) => {
    const sign = arrowSign(e.key);
    if (!sign) return;
    e.preventDefault();
    e.stopPropagation();
    // 10 pt further out (right / up) or in (left / down) at that end
    const d = rulerDir(ruler), at = rulerEnds(ruler)[which], out = which ? 10 * sign : -10 * sign;
    onRuler(stretchRuler(ruler, which, { x: at.x + d.x * out, y: at.y + d.y * out }));
  };
  const r = preview || ruler;
  const len = Math.max(r.length, 1);
  const deg = Math.round(r.angle) % 180;
  const ticks = rulerTicks(r, units, k);
  return (
    <div className="inkRuler" data-x={r.x} data-y={r.y} data-angle={r.angle} data-length={r.length}
      style={{ left: pct(r.x - r.length / 2, width), top: pct(r.y, height), width: pct(r.length, width), transform: `rotate(${-r.angle}deg)` }}>
      <div ref={bandRef} className="inkRulerBand" tabIndex={0} aria-label={t("Ruler")}
        title={t("Drag to move the ruler; write along its edges with a pen")}
        onPointerDown={(e) => begin(e, "move")} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel}
        onLostPointerCapture={cancel} onKeyDown={bandKey}>
        <svg className="inkRulerMarks" width="100%" height={RULER_PX} aria-hidden="true" focusable="false">
          <line className="inkRulerEdge" x1="0" y1="0.5" x2="100%" y2="0.5" />
          <line className="inkRulerEdge" x1="0" y1={RULER_PX - 0.5} x2="100%" y2={RULER_PX - 0.5} />
          {ticks.map((tick) => (
            <line key={tick.at} className="inkRulerTick" x1={pct(tick.at, len)} x2={pct(tick.at, len)} y1="1" y2={1 + TICK_PX[tick.size]} />
          ))}
          {ticks.filter((tick) => tick.label).map((tick) => (
            <text key={`n${tick.at}`} className="inkRulerNum" x={pct(tick.at, len)} y={TICK_PX[2] + 11} textAnchor="middle">{tick.label}</text>
          ))}
        </svg>
      </div>
      <span className="inkRulerSpot" style={{ left: "50%" }}>
        <button type="button" className="inkRulerKnob" role="slider" aria-label={t("Ruler angle")} aria-valuemin={0} aria-valuemax={179}
          aria-valuenow={deg} aria-valuetext={`${deg}°`} title={t("Drag to turn the ruler; tap to snap to 15°; double tap to turn it 90°")}
          style={{ transform: `translate(-50%, -50%) rotate(${r.angle}deg)` }}
          onPointerDown={(e) => begin(e, "turn")} onPointerMove={move} onPointerUp={(e) => { up(e); twice.onPointerUp(e); }}
          onDoubleClick={twice.onDoubleClick} onPointerCancel={cancel} onLostPointerCapture={cancel} onKeyDown={knobKey}>
          {deg}°
        </button>
      </span>
      {[0, 1].map((which) => (
        <span key={which} className="inkRulerSpot" style={{ left: which ? "100%" : "0%" }}>
          <button type="button" className="inkRulerEnd" data-end={which} aria-label={t("Ruler end")}
            title={t("Drag to lengthen or shorten the ruler")}
            onPointerDown={(e) => begin(e, "end", { end: which })} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel}
            onLostPointerCapture={cancel} onKeyDown={endKey(which)} />
        </span>
      ))}
    </div>
  );
}

// The zoom window's box on its page (`zoom`, ink/zoomWindow.js), in the
// selection blue: the part of the page the panel under the viewer shows.
// Its top band drags the window and its corner resizes it (the panel keeps
// its size, so a wider box is a lower magnification, magForWidth, held to
// 2…4); both take the press in the capture phase, before the page's layers
// and the viewer see it, and keep their screen size. A drag previews here,
// the box drawn where the window would go (zoomWindow.js moveWindow and
// setMag, App's own rules) at most once a frame, and hands its patch to
// onZoom at the release — the window is the page tools' for every page, so
// a drag that moved it each frame would draw every page's layers again
// each frame. A focused band's arrow keys move the window 1 pt (Shift 10),
// a focused corner's step its magnification, each press at once. The box
// itself takes no pointer: the page under it is written on as ever.
function InkZoomRect({ wrapRef, page, zoom, width, height, onZoom }) {
  const drag = useRef(null);
  const [preview, setPreview] = useState(null);
  useEffect(() => () => { if (drag.current) cancelAnimationFrame(drag.current.raf); drag.current = null; }, []);
  const size = { width, height };
  const begin = (e, mode) => {
    if (e.button !== 0 || drag.current || !wrapRef.current) return;
    e.preventDefault(); e.stopPropagation();
    const rect = wrapRef.current.getBoundingClientRect();
    drag.current = { id: e.pointerId, mode, k: rect.width / width, x: e.clientX, y: e.clientY, win: zoom, raf: 0, patch: null };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const move = (e) => {
    const d = drag.current;
    if (d?.id !== e.pointerId) return;
    e.preventDefault(); e.stopPropagation();
    const dx = (e.clientX - d.x) / d.k, dy = (e.clientY - d.y) / d.k;
    d.patch = d.mode === "move" ? { x: d.win.x + dx, y: d.win.y + dy } : { mag: magForWidth(d.win, d.win.w + dx) };
    if (!d.raf) d.raf = requestAnimationFrame(() => {
      d.raf = 0;
      if (drag.current === d && d.patch) setPreview(d.mode === "move" ? moveWindow(d.win, d.patch, size) : setMag(d.win, d.patch.mag, null, size));
    });
  };
  const end = (e) => {
    const d = drag.current;
    if (d?.id !== e.pointerId) return;
    drag.current = null;
    cancelAnimationFrame(d.raf);
    setPreview(null);
    if (d.patch) onZoom(page, d.patch);
  };
  const keys = (e, mode) => {
    const step = ARROWS[e.key];
    if (!step) return;
    e.preventDefault(); e.stopPropagation();
    const n = e.shiftKey ? 10 : 1;
    if (mode === "move") onZoom(page, { x: zoom.x + step[0] * n, y: zoom.y + step[1] * n });
    else onZoom(page, { mag: zoom.mag - (step[0] + step[1]) * 0.25 });
  };
  const handle = (mode, label, title) => (
    <button type="button" className={mode === "move" ? "inkZoomBand" : "inkZoomResize"} aria-label={label} title={title}
      onPointerDownCapture={(e) => begin(e, mode)} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
      onLostPointerCapture={end} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => keys(e, mode)} />
  );
  const box = preview || zoom;
  return (
    <div className="inkZoomRect" data-x={box.x} data-y={box.y} data-w={box.w} data-h={box.h}
      style={{ left: pct(box.x, width), top: pct(box.y, height), width: pct(box.w, width), height: pct(box.h, height) }}>
      {handle("move", t("Zoom window: drag to move"), t("Zoom window: drag to move it; the arrow keys move it too"))}
      {handle("resize", t("Zoom window: drag to resize"), t("Zoom window: drag to resize it; a smaller box writes larger"))}
    </div>
  );
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
    {/* no colour (a selection's fill of None, or mixed): nothing is marked */}
    <label className={"colorBtn inkSwatch inkCustomColor" + (listed || !current ? "" : " selected")}
      title={t("Custom color")} style={{ "--ink-custom": color || undefined }}>
      <input type="color" value={current || "#000000"} aria-label={t("Custom color")} onChange={(e) => onColor(e.target.value.toLowerCase(), alpha)} />
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

// The box an ink layer is seen through: the zoom window's view, a PDF's
// viewer, else the nearest ancestor that scrolls (a notebook's viewer, the
// notes), else the window (null).
function viewportOf(el) {
  const pdf = el.closest(".inkZoomView, .pdfViewer");
  if (pdf) return pdf;
  for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
    const { overflowY } = getComputedStyle(n);
    if (overflowY === "auto" || overflowY === "scroll") return n;
  }
  return null;
}
// That box's screen rect (the window's for null).
const viewRect = (view) => (view ? view.getBoundingClientRect() : { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight });
// A canvas over a layer laid on its window `win` (canvasSize.js
// canvasWindow) of the layer's screen rect `rect`: its box placed in the
// layer's own percentages (so a transform scaling the layer, the viewer's
// pinch, scales it too) and its backing store at the device's ratio within
// canvasSize's caps, allocated again — and so blank — when its size
// changes, or always when `fresh`.
function fitCanvas(canvas, win, rect, fresh = false) {
  const size = canvasSize(win.width, win.height, window.devicePixelRatio || 1);
  if (fresh || canvas.width !== size.width || canvas.height !== size.height) {
    canvas.width = 0; canvas.height = size.height; canvas.width = size.width;
  }
  Object.assign(canvas.style, { left: pct(win.x, rect.width), top: pct(win.y, rect.height), width: pct(win.width, rect.width),
    height: pct(win.height, rect.height), display: "block" });
}
// A canvas fitCanvas laid, its backing store released and the canvas hidden.
function releaseCanvas(canvas) {
  canvas.width = 0; canvas.height = 0;
  canvas.style.display = "none";
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
      const viewport = viewRect(view);
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
// hard cap for every later addition), the first fixed — Color, Width,
// Style, Copy, Duplicate, More, Delete — and More adding the second: Cut,
// Flip horizontal, Flip vertical, New note — Merge into note in its place
// when `canMerge` (New note would change nothing then) —, Select note,
// Show note and Transcribe. The Color, Width and Style rows open under
// whichever rows show, one at a time. `keys` names the chords the titles
// carry ("Copy · Ctrl+C"; the label alone unbound). `ignoreRef`: what a
// press in leaves the menu open (the page, and the zoom window, whose
// layer drags the selection too).
function InkSelectionMenu({ wrapRef, ignoreRef, box, width, strokes, canMerge, keys, onAction, onClose }) {
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
  // The Fill the selected closed shapes share (null: none; undefined: mixed).
  const closed = strokes.filter(isClosedShape);
  const fill = closed.length && closed.every((s) => (s.fill?.color ?? null) === (closed[0].fill?.color ?? null)) ? closed[0].fill?.color ?? null : undefined;
  const toggleOptions = (row) => { setStyleMenu(null); setOptions(options === row ? null : row); };
  const toggleStyleMenu = (button) => {
    styleBtnRef.current = button;
    const r = button.getBoundingClientRect();
    setStyleMenu((open) => (open ? null : { x: r.left, y: r.bottom + 4 }));
  };
  return <ContextMenu {...anchor} ignoreRef={ignoreRef} onClose={onClose} className="inkEditMenu">
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
        {canMerge ? <button className="ctlBtn" aria-label={t("Merge into note")} title={t("Merge this note's handwriting into the previous note on this page")} onClick={() => onAction("merge")}><MergeIcon aria-hidden="true" /></button>
          : <button className="ctlBtn" aria-label={t("New note")} title={t("New note from the selected handwriting")} onClick={() => onAction("split")}><NotebookPenIcon aria-hidden="true" /></button>}
        <button className="ctlBtn" aria-label={t("Select note")} title={t("Select all handwriting in this note")} onClick={() => onAction("select-note")}><RectSelectIcon aria-hidden="true" /></button>
        <button className="ctlBtn" aria-label={t("Show note")} title={t("Show note")} onClick={() => onAction("show-note")}><FileTextIcon aria-hidden="true" /></button>
        <button className="ctlBtn" aria-label={t("Transcribe")} title={t("Transcribe with AI: the selected handwriting as text in the chat")} onClick={() => onAction("transcribe")}><SparklesIcon aria-hidden="true" /></button>
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
      {options === "style" ? <div className="inkEditOptions" aria-label={t("Selected ink style")}>
        {pens.length ? <InkStylePicker brush={penBrush} mixed={penBrush === undefined} open={!!styleMenu} title={t("Pen style")} onOpen={toggleStyleMenu} /> : null}
        <InkLineChoices value={line} onLine={(dash) => onAction("style", { dash })} />
        {closed.length ? <span className="inkFillChoices" role="group" aria-label={t("Fill")}>
          <PaintBucketIcon className="inkFillGlyph" size={14} aria-hidden="true" />
          <button type="button" className={"ctlBtn inkSegBtn" + (fill === null ? " modeActive" : "")} aria-pressed={fill === null}
            aria-label={t("No fill")} title={t("No fill")} onClick={() => onAction("style", { fill: null })}><span>{t("None")}</span></button>
          <ColorChoices colors={pickerColors(palette, "highlighter")} color={fill || ""} alpha={1}
            onColor={(color) => onAction("style", { fill: { color } })} />
        </span> : null}
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
