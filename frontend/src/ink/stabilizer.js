// The lazy brush behind a preset's `stabilize` (docs/dev/handwriting.md
// "Smoothing" and "Client"): the pen tip pulls the drawn point behind it on
// a string of R css px, as Krita's and Xournal++'s stabilizers do, so a
// shaky hand draws a calm line. Pure and per stroke: InkLayer makes one at
// the pen-down and feeds every sample through `push` before the sample
// reaches the stroke, then `finish` catches the line up to the lift point.
// Pressure and time ride through untouched; nothing here reads the style.

// The string's length per level, css px (divided by k to points).
export const STABILIZE_RADII = [0, 3, 6, 10, 16];
export const MAX_STABILIZE = STABILIZE_RADII.length - 1;
const CATCH_UP = 8;        // at most this many samples catch the line up at the lift
const CUSP_RETREAT = 0.5;  // a cusp is real once the pointer has retreated R × this from its tip, along the brush's last move

const passthrough = { push: (s) => s, finish: (s) => [s] };

// level 0..4 (0 or an unknown level: every sample passes through); k css px
// per pt. → {push(sample) → sample | null, finish(sample) → sample[]}.
export function makeStabilizer(level, { k = 1 } = {}) {
  const R = (STABILIZE_RADII[level] ?? 0) / k;
  if (!(R > 0)) return passthrough;
  let brush = null;          // where the line is
  let pointer = null;        // the previous pointer sample
  let move = { x: 0, y: 0 }; // the brush's last move
  let taut = false;          // the last push moved the brush (the string is taut)
  let tip = null;            // a cusp pending: where the pointer turned back
  let lastT = 0;             // the t of the last emitted sample
  const emit = (s, x, y) => { lastT = s.t ?? lastT; return { ...s, x, y }; };
  // The brush jumps to the pending tip; the string hangs slack from there.
  const jump = () => { move = { x: tip.x - brush.x, y: tip.y - brush.y }; brush = { x: tip.x, y: tip.y }; tip = null; taut = false; };

  return {
    push(s) {
      if (!brush) {
        brush = { x: s.x, y: s.y };
        pointer = s;
        return emit(s, s.x, s.y);
      }
      const prev = pointer;
      pointer = s;
      // the cusp rule: the pointer turning back more than 90° against the
      // brush's last move (with the string taut) makes where it turned the
      // tip of a cusp; once the pointer has retreated CUSP_RETREAT × R from
      // it along that move the brush jumps there and the tip is emitted, so
      // a corner stays a corner. A smaller retreat is tremor and emits
      // nothing; a pointer passing the tip again forgets it, and so does one
      // that leaves the tip sideways by more than R (a shake across the
      // stroke, which the string must smooth, not copy).
      if (!tip && taut && (s.x - prev.x) * move.x + (s.y - prev.y) * move.y < 0) tip = { x: prev.x, y: prev.y };
      if (tip) {
        const retreat = -((s.x - tip.x) * move.x + (s.y - tip.y) * move.y) / (Math.hypot(move.x, move.y) || 1);
        if (retreat <= 0) tip = null;
        else if (retreat >= CUSP_RETREAT * R) { jump(); return emit(s, brush.x, brush.y); }
        else if (Math.hypot(s.x - tip.x, s.y - tip.y) <= R) return null;
        else tip = null;
      }
      const dx = s.x - brush.x, dy = s.y - brush.y, d = Math.hypot(dx, dy);
      if (d <= R) { taut = false; return null; }
      const f = (d - R) / d;
      move = { x: dx * f, y: dy * f };
      brush = { x: brush.x + move.x, y: brush.y + move.y };
      taut = true;
      return emit(s, brush.x, brush.y);
    },
    // The lift: a pending tip first, then at most CATCH_UP samples along the
    // quadratic from the brush (control point: the brush plus half its last
    // move) to the lift point, spaced about R / 2, p the lift's, t spread
    // from the last emitted t to the lift's, ending with the lift point
    // exactly.
    finish(s) {
      if (!brush) return [s];
      const pts = [];
      if (tip && (tip.x !== brush.x || tip.y !== brush.y)) { jump(); pts.push({ x: brush.x, y: brush.y }); }
      tip = null;
      const d = Math.hypot(s.x - brush.x, s.y - brush.y);
      if (d) {
        const n = Math.min(CATCH_UP, Math.max(1, Math.ceil(d / (R / 2))));
        const p0 = brush, p1 = { x: brush.x + move.x / 2, y: brush.y + move.y / 2 };
        for (let i = 1; i < n; i++) {
          const u = i / n, v = 1 - u;
          pts.push({ x: v * v * p0.x + 2 * v * u * p1.x + u * u * s.x, y: v * v * p0.y + 2 * v * u * p1.y + u * u * s.y });
        }
        pts.push({ x: s.x, y: s.y });
      }
      if (!pts.length) return [s];
      const t0 = lastT, t1 = s.t ?? lastT, m = pts.length;
      const out = pts.map((q, i) => ({ ...s, x: q.x, y: q.y, ...(s.t != null ? { t: i === m - 1 ? s.t : t0 + ((t1 - t0) * (i + 1)) / m } : {}) }));
      brush = { x: s.x, y: s.y };
      taut = false;
      lastT = t1;
      return out;
    },
  };
}
