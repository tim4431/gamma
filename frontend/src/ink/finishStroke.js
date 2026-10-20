// The one stage between a lifted pen and the committed stroke
// (docs/dev/handwriting.md "Client"). Pure: InkLayer's finish and the iPad
// host's claim.commit both hand their samples here, so whatever a finished
// stroke goes through — today decimation, then the width bake — happens in
// one place, in this order: the hold result (a shape the layer recognised
// while the pen stood still), the straight highlighter, the scribble that
// erases, then decimation and the width bake. `commit` is what to encode,
// `erase` names strokes a gesture removes, `label` the history entry,
// `style` what to encode with and `status` the pill.
import { T, t } from "../shared/i18n/i18n.js";
import { shapeName, shapeSamples } from "./shapes.js";
import { isScribble, scribbleTargets } from "./gestures.js";

// Coalesced samples closer than this to the previous kept one (in points,
// scale 1) are dropped: a 240 Hz trace roughly halves with no visible change.
export const DECIMATE_PT = 0.1;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// The brush table — the only place a width law lives. A tool's brushes
// ("" is the tool's own) map to the law giving a sample's drawn-width
// multiplier w (the stroke's size times w is the width drawn, ink.js
// strokeWidth), or to null for a constant width (no w channel). The default
// pen's law is perfect-freehand's on-screen value at thinning 0.5,
// 1 + (p − 0.5), capped to [0, 2]; the raw pressure and tilt stay in the
// samples beside w, so a later brush can re-bake from them.
const LAWS = {
  pen: { "": (s) => clamp(1 + ((s.p ?? 0.5) - 0.5), 0, 2), monoline: null },
  highlighter: { "": null },
};

// The width law for a style ({tool, brush, pen}): a function of a sample,
// or null when the stroke draws at its nominal size (highlighter, monoline,
// a mouse or finger stroke, an unknown tool or brush).
export function widthLaw({ tool = "pen", brush, pen = true } = {}) {
  if (pen === false) return null;
  const laws = LAWS[tool];
  return laws ? laws[brush || ""] ?? null : null;
}

// The channel list a new stroke of `style` is written with: pressure for a
// pen, time always, w when the style has a width law.
export function strokeChannels(style) {
  return "xy" + (style.pen === false ? "" : "p") + "t" + (widthLaw(style) ? "w" : "");
}

// Drop samples closer than `min` to the previous kept one. The first and
// the last sample stay (the last is the lift, so the stroke reaches the
// pen tip); the input comes back untouched when nothing is dropped.
export function decimate(samples, min = DECIMATE_PT) {
  if (samples.length < 3) return samples;
  const out = [samples[0]];
  for (let i = 1; i < samples.length - 1; i++) {
    const s = samples[i], k = out[out.length - 1];
    if (Math.hypot(s.x - k.x, s.y - k.y) >= min) out.push(s);
  }
  const last = samples[samples.length - 1], k = out[out.length - 1];
  if (out.length > 1 && Math.hypot(last.x - k.x, last.y - k.y) < min) out[out.length - 1] = last;
  else out.push(last);
  return out.length === samples.length ? samples : out;
}

// Bake the drawn width into the samples as w, from the style's law; a
// style without one leaves the samples as they are (no w is written).
export function bakeWidths(samples, style) {
  const law = widthLaw(style);
  if (!law) return samples;
  return samples.map((s) => ({ ...s, w: Math.round(law(s) * 1000) / 1000 }));
}

// The chord of a highlight: a straight highlighter preset's "Always" replaces
// the stroke by its chord when every sample lies within min(0.75 × size,
// STRAIGHT_CAP_PT) of it and the chord is at least STRAIGHT_MIN_SIZES widths
// long, snapped horizontal within STRAIGHT_SNAP_DEG. → {x1, y1, x2, y2} | null.
const STRAIGHT_CAP_PT = 6;
const STRAIGHT_MIN_SIZES = 4;
const STRAIGHT_SNAP_DEG = 4;
export function straightChord(samples, size) {
  const a = samples[0], b = samples[samples.length - 1];
  const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
  if (!(len >= STRAIGHT_MIN_SIZES * size)) return null;
  const corridor = Math.min(0.75 * size, STRAIGHT_CAP_PT);
  for (const s of samples) if (Math.abs((s.x - a.x) * dy - (s.y - a.y) * dx) / len > corridor) return null;
  const deg = Math.atan2(dy, dx) * 180 / Math.PI;
  const flat = Math.abs(deg) <= STRAIGHT_SNAP_DEG || Math.abs(Math.abs(deg) - 180) <= STRAIGHT_SNAP_DEG;
  if (!flat) return { x1: a.x, y1: a.y, x2: b.x, y2: b.y };
  const my = (a.y + b.y) / 2, half = len / 2, mx = (a.x + b.x) / 2, sign = Math.sign(dx) || 1;
  return { x1: mx - sign * half, y1: my, x2: mx + sign * half, y2: my };
}

const r2 = (v) => Math.round(v * 100) / 100;
// The shape record a snapped stroke carries: a circle is written as an
// ellipse with equal radii, since the file's kinds (and the server's
// SHAPE_KINDS) have no circle; `circle` stays the recogniser's answer for
// the pill and the refit.
function shapeRecord(kind, params) {
  if (kind === "circle") return { kind: "ellipse", params: { cx: params.cx, cy: params.cy, rx: params.r, ry: params.r, angle: 0 } };
  return { kind, params: { ...params } };
}

// samples: [{x, y, p?, t}] in page units, already stabilised; ctx: {style:
// {tool, brush, color, size, opacity}, pen: a stylus drew it, k: css px per
// pt, hold: the layer's hold result {kind, params} | null, straight: a
// highlighter preset's 'hold' | 'always' | 'off', scribble: scribble-to-erase
// is on, groups: the page's [{id, ink}], keep(stroke): the eraser filter}.
// Every key but style is optional. → {commit: the samples to encode (null
// for nothing), erase: [{gid, ids}] the gesture removes (null for none),
// label: the history entry's label, style: the object to encode with
// (ctx.style itself when nothing snapped), status: {text, once} for the
// pill, or null}.
export function finishStroke(samples, ctx) {
  const plain = { commit: null, erase: null, label: T("ink stroke"), style: ctx.style, status: null };
  if (!samples?.length) return plain;
  const style = { ...ctx.style, pen: ctx.pen !== false };
  const tool = style.tool || "pen";
  const first = samples[0], last = samples[samples.length - 1];
  const span = Math.max(0, (last.t ?? 0) - (first.t ?? 0));
  const meanP = samples.reduce((s, q) => s + (q.p ?? 0.5), 0) / samples.length;

  // 1. the hold: the layer recognised a shape while the pen stood still
  if (ctx.hold?.kind) {
    const { kind, params } = ctx.hold;
    const commit = shapeSamples(kind, params, { span, p: r2(meanP) });
    if (commit.length) {
      const line = kind === "line" || kind === "arrow";
      return { commit, erase: null,
        label: line ? T("ink straight line") : T("ink shape"),
        style: { ...ctx.style, pen: false, shape: shapeRecord(kind, params) },
        status: { text: line ? t("Straightened.") : t("Shape: {kind}.", { kind: shapeName(kind) }), once: false } };
    }
  }

  // 2. the straight highlighter's "Always": the chord when the stroke is straight enough
  if (tool === "highlighter" && ctx.straight === "always" && samples.length >= 2) {
    const chord = straightChord(samples, style.size ?? 14);
    if (chord) {
      const p = r2(meanP);
      return { commit: [{ x: chord.x1, y: chord.y1, p, t: 0 }, { x: chord.x2, y: chord.y2, p, t: span }], erase: null,
        label: T("ink straight line"),
        style: { ...ctx.style, pen: false, shape: { kind: "line", params: { x1: r2(chord.x1), y1: r2(chord.y1), x2: r2(chord.x2), y2: r2(chord.y2) } } },
        status: { text: t("Straightened."), once: true } };
    }
  }

  // 3. the scribble: a fast dense back-and-forth with a pen erases what is under it
  if (ctx.scribble && tool === "pen" && isScribble(samples, { k: ctx.k ?? 1 })) {
    const erase = scribbleTargets(samples, ctx.groups || [], { keep: ctx.keep });
    if (erase.length) return { commit: null, erase, label: T("ink scribble erase"), style: ctx.style, status: null };
  }

  // 4. decimate, then bake the width
  return { ...plain, commit: bakeWidths(decimate(samples), style) };
}
