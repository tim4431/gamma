// The one stage between a lifted pen and the committed stroke
// (docs/dev/handwriting.md "Client"). Pure: InkLayer's finish and the iPad
// host's claim.commit both hand their samples here, so whatever a finished
// stroke goes through — today decimation, then the width bake — happens in
// one place. Draw-and-hold straightening, scribble-to-erase and shape
// recognition are later stages of the same function: `erase` names strokes
// the gesture removes and `label` the history entry it makes.
import { T } from "../shared/i18n/locales.js";

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

// samples: [{x, y, p?, t}] in page units; ctx: {style: {tool, brush, color,
// size, opacity}, pen: a stylus drew it}. → {commit: the samples to encode
// (null for nothing), erase: [{gid, ids}] the gesture removes (null for
// none), label: the history entry's label}.
export function finishStroke(samples, ctx) {
  const label = T("ink stroke");
  if (!samples?.length) return { commit: null, erase: null, label };
  const style = { ...ctx.style, pen: ctx.pen !== false };
  const commit = bakeWidths(decimate(samples), style);
  return { commit, erase: null, label };
}
