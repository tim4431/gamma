// The clocks a replay runs on (replay/playback.js; docs/dev/recording.md
// "One clock for every replay"). A clock has its own time `t`, ms from its
// start up to `duration`, and maps it to the strokes' wall clock and back:
// `toWall(t)` the moment being replayed, `toT(wall)` where a moment falls
// on it (null outside it). `times`, when a clock has one, places strokes on
// its own time (ink.js inkUntil's table); a clock without one places them
// by their wall clock. Pure; tests/replay.test.mjs.
import { inkTimeline, strokeWalls } from "../ink/ink.js";

// A group's (or a sheet's) own replay: inkTimeline's compressed clock,
// pauses shrunk and the whole scaled into a few seconds. `toWall` reads the
// real moment of what is drawn at t, for a readout.
export function compressedClock(ink, opts) {
  const line = inkTimeline(ink, opts);
  const items = line.strokes.map((item) => ({ ...item, walls: strokeWalls(ink.strokes[item.index]) }));
  return {
    kind: "compressed",
    duration: line.duration,
    times: line.times,
    toWall(t) {
      let found = null;
      for (const item of items) {
        if (item.start > t) break;
        if (item.walls) found = item;
      }
      if (!found) return null;
      let k = 0;
      while (k + 1 < found.times.length && found.times[k + 1] <= t) k++;
      return found.walls[k];
    },
    toT(wall) {
      let best = null;
      for (const item of items) {
        if (!item.walls || item.walls[0] > wall) continue;
        let k = 0;
        while (k + 1 < item.walls.length && item.walls[k + 1] <= wall) k++;
        if (best == null || item.times[k] > best) best = item.times[k];
      }
      return best;
    },
  };
}

// A recording's clock: its own time is the audio's, the segments laid end
// to end with the pauses between them left out. `span` is the wall-clock
// stretch it covers; a moment inside a pause maps to the resume point;
// `segmentAt(t)` names the segment to play and the offset into it.
export function recordingClock(recording) {
  const segments = [];
  let acc = 0;
  for (const seg of recording?.segments || []) {
    if (!(seg.dur > 0)) continue;
    segments.push({ ...seg, start: acc });
    acc += seg.dur;
  }
  const first = segments[0], last = segments[segments.length - 1];
  const from = first ? first.started : recording?.started ?? 0;
  const to = last ? last.started + last.dur : from;
  return {
    kind: "recording",
    duration: acc,
    segments,
    span: [from, to],
    toWall(t) {
      const at = segmentAt(segments, acc, t);
      return at ? at.segment.started + at.offset : null;
    },
    toT(wall) {
      if (!segments.length || wall < from || wall > to) return null;
      for (const seg of segments) {
        if (wall < seg.started) return seg.start;
        if (wall <= seg.started + seg.dur) return seg.start + (wall - seg.started);
      }
      return acc;
    },
    segmentAt: (t) => segmentAt(segments, acc, t),
  };
}

function segmentAt(segments, duration, t) {
  if (!segments.length) return null;
  const at = Math.max(0, Math.min(duration, t));
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (at < seg.start + seg.dur || i === segments.length - 1) return { index: i, segment: seg, offset: Math.min(seg.dur, at - seg.start) };
  }
  return null;
}

// When something was written during a recording, as ticks on its clock:
// each timed stroke's start and each block stamped with a `t0`, inside the
// clock's span, sorted by t. `groups`: [{id, ink}]; `blocks`: [{id, t0}].
// → [{t, wall, kind: "ink" | "block", id, group?}]
export function writingTicks(clock, groups = [], blocks = []) {
  const out = [];
  const [from, to] = clock.span || [-Infinity, Infinity];
  for (const { id: group, ink } of groups) {
    for (const stroke of ink?.strokes || []) {
      const ts = strokeWalls(stroke);
      if (!ts || ts[0] < from || ts[0] > to) continue;
      const t = clock.toT(ts[0]);
      if (t != null) out.push({ t, wall: ts[0], kind: "ink", id: stroke.id, group });
    }
  }
  for (const { id, t0 } of blocks) {
    if (!Number.isFinite(t0) || t0 < from || t0 > to) continue;
    const t = clock.toT(t0);
    if (t != null) out.push({ t, wall: t0, kind: "block", id });
  }
  return out.sort((a, b) => a.t - b.t);
}

// The tick before / after t, for the player's writing skips: `dir` −1 or 1.
// A tick within `slack` ms of t does not count as "the next one".
export function nextTick(ticks, t, dir, slack = 500) {
  if (dir > 0) return ticks.find((k) => k.t > t + slack) || null;
  for (let i = ticks.length - 1; i >= 0; i--) if (ticks[i].t < t - slack) return ticks[i];
  return null;
}

// m:ss, or h:mm:ss past an hour.
export function fmtClock(ms) {
  const total = Math.max(0, Math.round((ms || 0) / 1000));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}
