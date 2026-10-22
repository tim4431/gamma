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
import {
  DEFAULT_NIB, DEFAULT_SIZE, KNOWN_BRUSHES, MAX_STROKE_SIZE, NIB_HAIR, arcLengths, clamp, decodeStroke, encodePoints, round2, round3,
} from "./ink.js";

// Coalesced samples closer than this to the previous kept one (in points,
// scale 1) are dropped: a 240 Hz trace roughly halves with no visible change.
export const DECIMATE_PT = 0.1;

// The brush table (docs/dev/handwriting.md "The brush table";
// tests/shared/inkwidth.json pins every law, its `about` the normative
// statement) — the only place a width or an opacity law lives. A pen's
// brush (`pen` is the plain Pen, the absent key) maps to `w`, a sample's
// drawn-width multiplier (size × w is the width drawn, ink.js strokeWidth),
// and `o`, its opacity multiplier (opacity × o, ink.js strokeOpacity); a
// brush without `w` draws at its nominal size and writes no w channel. The
// Brush's `w` is shaped along the stroke by its two tapers (in sizes of
// arc length, each at most half the stroke, eased out as a sine) between
// `min` and `max`, and when the pointer reports no pressure its `simulate`
// speed law stands in for it. The Calligraphy pen's law reads the stroke's
// travel at the sample (`travel`: travelAt) and its `nib` as well, so a
// mouse or finger stroke has it too, its pressure read as 0.5.
// A highlighter, an unknown tool, an unknown brush and a stroke that
// carries a shape record (a held shape, a straight highlight) have no laws:
// a shape is constant width whatever its brush, so it keeps its brush key
// but no w or o. The raw pressure and tilt stay in the samples beside w
// and o, so restyleBrush bakes them again for another brush.
const pOf = (s) => clamp(s.p ?? 0.5, 0, 1);
export const BRUSHES = {
  // perfect-freehand's on-screen value at thinning 0.5; tilt is ignored
  pen: { w: (s) => clamp(1 + (pOf(s) - 0.5), 0, 2) },
  // Ballpoint: an even line at every pressure
  monoline: {},
  // a felt tip: twice as wide at full pressure, thin at none
  brush: { w: (s) => 2 * pOf(s), taper: { start: 2, end: 3 }, min: 0.1, max: 2, simulate: true },
  // graphite: the pen's width grown by up to three times as the pencil
  // leans from upright (a 90°, the default when no tilt was reported) to
  // flat, capped at 4; pressure darkens it
  pencil: {
    w: (s) => clamp((1 + (pOf(s) - 0.5)) * (1 + 2 * (1 - clamp(s.a ?? 90, 0, 90) / 90)), 0, 4),
    o: (s) => clamp(0.15 + 0.85 * pOf(s), 0, 1),
  },
  // a flat nib: as wide as the nib where the stroke travels across it and
  // a hairline (NIB_HAIR) along it — the sine between the travel `dir`
  // ([dx, dy] in page points, y down; null for none, which draws the
  // nib's whole length) and the nib's edge, the page vector (cos nib, −sin
  // nib) — and a little wider with pressure, capped at 4
  calligraphy: {
    w: (s, dir, nib = DEFAULT_NIB) => {
      const r = (nib * Math.PI) / 180;
      const across = dir ? Math.abs(dir[0] * Math.sin(r) + dir[1] * Math.cos(r)) / Math.hypot(dir[0], dir[1]) : 1;
      return clamp(Math.max(NIB_HAIR, across) * (0.85 + 0.3 * pOf(s)), 0, 4);
    },
    travel: true,
  },
};
// The direction a stroke travels at sample i, [dx, dy] in page points:
// from the sample before to the sample after (one-sided at the ends);
// where those two coincide (a stroke turning straight back) the segment
// ending at the sample, else the one starting there; null where the stroke
// has no length (a tap, every sample at one point).
export function travelAt(samples, i) {
  const n = samples.length;
  const at = (a, b) => [samples[b].x - samples[a].x, samples[b].y - samples[a].y];
  const tries = [at(Math.max(i - 1, 0), Math.min(i + 1, n - 1))];
  if (i > 0) tries.push(at(i - 1, i));
  if (i < n - 1) tries.push(at(i, i + 1));
  return tries.find(([dx, dy]) => Math.hypot(dx, dy) > 1e-9) || null;
}
// perfect-freehand's simulatePressure step (its RATE_OF_PRESSURE_CHANGE).
const SIMULATE_RATE = 0.275;
const easeOutSine = (x) => Math.sin(x * Math.PI / 2);

// A style's ({tool, brush, pen}) entry in the table, or null.
function brushOf({ tool = "pen", brush } = {}) {
  if (tool !== "pen") return null;
  if (!brush || brush === "pen") return BRUSHES.pen;
  return KNOWN_BRUSHES.has(brush) ? BRUSHES[brush] : null;
}

// The width law of a style, a function of one sample before the Brush's
// tapers and speed law (bakeWidths applies those) — the Calligraphy pen's
// of the sample, its travel and the nib (brushWidths hands it all three) —
// or null when the style writes no w: a highlighter, the Ballpoint, a
// mouse or finger stroke of every brush but the Brush and the Calligraphy
// pen, an unknown tool or brush, and a stroke with a `shape` (constant
// width, whatever its brush: a Brush's tapers would put both ends of a
// two-sample line at the floor, and every renderer draws between its
// samples).
export function widthLaw(style = {}) {
  const b = brushOf(style);
  if (!b?.w || style.shape || (style.pen === false && !b.simulate && !b.travel)) return null;
  return b.w;
}
// The opacity law of a style, or null when it writes no o (every brush but
// a stylus's Pencil, and no stroke with a `shape`).
export function opacityLaw(style = {}) {
  const b = brushOf(style);
  return b?.o && !style.shape && style.pen !== false ? b.o : null;
}

// Whether a stroke of `style` has a pressure to read: a stylus drew it
// (`pen` not false) and its samples carry p.
const hasPressure = (style, samples) => style.pen !== false && samples.length > 0 && samples[0].p != null;
const NO_PRESSURE = { p: 0.5 };
// The pressure the speed law gives each sample, from the spacing alone
// (perfect-freehand's simulatePressure; time is never read): 0.5 at the
// first, then a step toward 1 − min(1, d / size) for a sample at distance d
// from the one before, so slow is wide and fast is thin.
function simulatedPressure(samples, size) {
  const out = new Array(samples.length);
  let p = 0.5;
  for (let i = 0; i < samples.length; i++) {
    if (i) {
      const sp = Math.min(1, Math.hypot(samples[i].x - samples[i - 1].x, samples[i].y - samples[i - 1].y) / size);
      p = Math.min(1, p + (1 - sp - p) * sp * SIMULATE_RATE);
    }
    out[i] = p;
  }
  return out;
}

// The multipliers a stroke of `style` is baked with, one per sample to
// three decimals, or null when the style writes no w. The Brush's tapers
// run over the samples' arc length: the start taper while the arc is under
// 2 sizes, the end taper while the rest is under 3 sizes, each at most half
// the stroke's length, so a short mark keeps its width in the middle, and
// neither on a stroke of no length (a tap draws the law's width, as
// perfect-freehand draws a lone point at its untapered radius); its
// pressure is the samples' own, else (no pressure: a mouse or finger, the
// pressure preference off, a stroke without p) the speed law. A style with
// a `shape` has no law (widthLaw), so a snapped stroke gets null here. The
// Calligraphy pen reads each sample's travel (travelAt) and the style's
// `nib` (DEFAULT_NIB without), the pressure only when the stroke reads it.
// `live` is the stroke in progress (the live canvas, a peer's wet stroke),
// whose length is not known yet: no end taper, and the start taper at its
// full length, so the line under the pen holds still while it is written
// (a short mark takes its shorter tapers at the lift).
export function brushWidths(samples, style, { live = false } = {}) {
  const law = widthLaw(style);
  if (!law || !samples.length) return null;
  const b = brushOf(style);
  if (b.travel) {
    const read = hasPressure(style, samples), nib = Number.isFinite(style.nib) ? style.nib : DEFAULT_NIB;
    return samples.map((s, i) => round3(law(read ? s : NO_PRESSURE, travelAt(samples, i), nib)));
  }
  if (!b.taper) return samples.map((s) => round3(law(s)));
  const size = Math.max(0.01, style.size ?? DEFAULT_SIZE);
  const simulated = hasPressure(style, samples) ? null : simulatedPressure(samples, size);
  const arc = arcLengths(samples);
  const total = arc[arc.length - 1], most = live ? Infinity : total / 2;
  const start = Math.min(b.taper.start * size, most), end = Math.min(b.taper.end * size, most);
  return samples.map((s, i) => {
    const ts = arc[i] < start ? easeOutSine(arc[i] / start) : 1;
    const te = !live && total - arc[i] < end ? easeOutSine((total - arc[i]) / end) : 1;
    const w = law(simulated ? { p: simulated[i] } : s) * Math.min(ts, te);
    return round3(clamp(w, b.min, b.max));
  });
}
// The opacity multipliers of a stroke of `style`, or null (the pencil's
// shading from pressure).
export function brushOpacities(samples, style) {
  const law = opacityLaw(style);
  return law && samples.length ? samples.map((s) => round3(law(s))) : null;
}

// The channel list a new stroke of `style` is written with: pressure for a
// stylus, then tilt when any of `samples` reported it (the a / z letters
// are written only then, so a tilt-less client and a tilt-capable one write
// an untilted stroke alike), time always, w when the style has a width law
// and o when it has an opacity law: `xyptw` a pressure pen or brush,
// `xyptwo` a pencil (`xyptazwo` tilted), `xypt` a Ballpoint or a
// highlighter, `xyt` a mouse or finger stroke and every snapped result
// (pen false and a shape, whatever the brush), but `xytw` for a mouse
// brush's or calligraphy pen's freehand stroke.
export function strokeChannels(style, samples = []) {
  if (style.pen === false) return "xyt" + (widthLaw(style) ? "w" : "");
  return "xypt" + (samples.some((s) => s.a != null) ? "az" : "") + (widthLaw(style) ? "w" : "") + (opacityLaw(style) ? "o" : "");
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

// Bake the drawn width into the samples as w, and the pencil's opacity as
// o, from the style's laws (brushWidths, brushOpacities); a style without
// them leaves the samples as they are (no w or o is written).
export function bakeWidths(samples, style) {
  const w = brushWidths(samples, style), o = brushOpacities(samples, style);
  if (!w && !o) return samples;
  return samples.map((s, i) => {
    const out = { ...s };
    if (w) out.w = w[i];
    if (o) out.o = o[i];
    return out;
  });
}

// The selection's brush change (docs/dev/handwriting.md "Client"): every
// selected stroke of tool pen (`brush` null or 'pen' for the plain Pen,
// else 'monoline' | 'brush' | 'pencil' | 'calligraphy'; any other value
// changes nothing; a calligraphy pen bakes with the stroke's own `nib`,
// else DEFAULT_NIB, and writes it, any other brush drops it) is
// baked again — its samples decoded, w and o stripped, the new brush's laws
// run on the pressure and tilt it was written with, with its own `pen` flag
// (a mouse stroke turned Brush gets the speed law) — and written with its
// own channels but w and o, plus what the new brush writes (strokeChannels
// for every stroke this build writes; a letter this build does not know
// keeps its place), and the new `brush` key (none for the plain Pen); a
// stroke carrying a `shape` stays constant width (its style carries the
// shape, so widthLaw and opacityLaw are null): only its brush key changes,
// and a w or o an older build baked for it goes. A geometry change for the
// merge (ink.js mergeInk: the key travels with the widths baked for it). A
// w that would draw wider than MAX_STROKE_SIZE (a stroke resized large,
// then turned Pencil) is held there, as the server refuses a file past
// it. A stroke already in that brush whose widths are
// the ones the brush bakes, within BAKE_SLACK (the bake at the lift read
// the samples before the file rounded them to 1/100 pt), is the same
// object, so choosing its brush again changes nothing; one whose key and
// widths disagree (an older build's merge, a law since changed) is baked
// again. Highlighters are the same objects; the same `ink` comes back when
// nothing changed.
const BAKE_SLACK = 0.05;
const bakedAlike = (was, now) => was.every((q, i) => {
  const r = now[i];
  return (q.w == null) === (r.w == null) && (q.o == null) === (r.o == null)
    && (q.w == null || Math.abs(q.w - r.w) <= BAKE_SLACK) && (q.o == null || Math.abs(q.o - r.o) <= BAKE_SLACK);
});
export function restyleBrush(ink, ids, brush) {
  const next = brush == null || brush === "pen" ? null : brush;
  if (next !== null && !KNOWN_BRUSHES.has(next)) return ink;
  const selected = new Set(ids);
  let changed = false;
  const strokes = ink.strokes.map((s) => {
    if (!selected.has(s.id) || (s.tool || "pen") !== "pen") return s;
    const nib = next === "calligraphy" ? (Number.isFinite(s.nib) ? s.nib : DEFAULT_NIB) : undefined;
    const style = { tool: "pen", brush: next || undefined, size: s.size ?? DEFAULT_SIZE, pen: s.pen !== false, shape: s.shape, nib };
    const cap = MAX_STROKE_SIZE / style.size;
    const decoded = decodeStroke(s);
    const samples = bakeWidths(decoded.map(({ w, o, ...q }) => q), style).map((q) => (q.w > cap ? { ...q, w: Math.floor(cap * 1000) / 1000 } : q));
    if ((s.brush || null) === next && (s.nib ?? undefined) === nib && bakedAlike(decoded, samples)) return s;
    changed = true;
    const ch = [...(s.ch || "xy")].filter((c) => c !== "w" && c !== "o").join("")
      + (widthLaw(style) ? "w" : "") + (opacityLaw(style) ? "o" : "");
    const { brush: _was, nib: _nib, ...keys } = s;
    return { ...keys, ...(next ? { brush: next } : {}), ...(nib != null ? { nib } : {}), ch, pts: encodePoints(samples, ch) };
  });
  return changed ? { ...ink, strokes } : ink;
}

// The chord of a highlight: a straight highlighter preset's "Always" replaces
// the stroke by its chord when every sample lies within min(0.75 × size,
// STRAIGHT_CAP_PT) of it and the chord is at least STRAIGHT_MIN_SIZES widths
// long, snapped horizontal within STRAIGHT_SNAP_DEG. → {x1, y1, x2, y2} | null.
const STRAIGHT_CAP_PT = 6;
const STRAIGHT_MIN_SIZES = 4;
const STRAIGHT_SNAP_DEG = 4;
function straightChord(samples, size) {
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

  // 1. the hold: the layer recognised a shape while the pen stood still. An
  // even line (pen: false) in the preset's look, constant width whatever
  // its brush: a style with a shape has no laws, so nothing is baked (the
  // brush key stays, for the Style picker), and it is written xyt.
  if (ctx.hold?.kind) {
    const { kind, params } = ctx.hold;
    const shaped = shapeSamples(kind, params, { span, p: round2(meanP) });
    if (shaped.length) {
      const line = kind === "line" || kind === "arrow";
      // a nib only on a calligraphy pen's, and the preset's Lock angle never on a stroke
      const { nibLock: _lock, nib, ...keys } = ctx.style;
      const look = { ...keys, ...(keys.brush === "calligraphy" ? { nib: Number.isFinite(nib) ? nib : DEFAULT_NIB } : {}),
        pen: false, shape: shapeRecord(kind, params) };
      return { commit: shaped, erase: null,
        label: line ? T("ink straight line") : T("ink shape"),
        style: look,
        status: { text: line ? t("Straightened.") : t("Shape: {kind}.", { kind: shapeName(kind) }), once: false } };
    }
  }

  // 2. the straight highlighter's "Always": the chord when the stroke is straight enough
  if (tool === "highlighter" && ctx.straight === "always" && samples.length >= 2) {
    const chord = straightChord(samples, style.size ?? 14);
    if (chord) {
      const p = round2(meanP);
      return { commit: [{ x: chord.x1, y: chord.y1, p, t: 0 }, { x: chord.x2, y: chord.y2, p, t: span }], erase: null,
        label: T("ink straight line"),
        style: { ...ctx.style, pen: false, shape: { kind: "line", params: { x1: round2(chord.x1), y1: round2(chord.y1), x2: round2(chord.x2), y2: round2(chord.y2) } } },
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
