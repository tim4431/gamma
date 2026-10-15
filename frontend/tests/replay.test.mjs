// node --test tests/  (from frontend/) — the replay clocks (replay/clock.js)
// and the frame they drive (ink.js inkUntil), and the store's time keeping.
import assert from "node:assert/strict";
import { test } from "node:test";
import { decodeStroke, encodeStroke, inkAtTime, inkTimeline, inkUntil, inkWallSpan, newCanvasInk, strokeWalls } from "../src/ink/ink.js";
import { compressedClock, fmtClock, nextTick, recordingClock, writingTicks } from "../src/replay/clock.js";

const timed = (id, t0, pts, y = 0) => encodeStroke({ id, t0, ch: "xyt", samples: pts.map(([x, t]) => ({ x, y, t })) });
const canvas = (strokes) => ({ ...newCanvasInk(100, 100), strokes });

test("strokeWalls: each sample's wall-clock moment, none for a stroke without timing", () => {
  const a = timed("a", 5000, [[0, 0], [10, 100], [20, 250]]);
  assert.deepEqual(strokeWalls(a), [5000, 5100, 5250]);
  assert.equal(strokeWalls(a), strokeWalls(a), "cached per stroke");
  const bare = encodeStroke({ id: "u", ch: "xy", samples: [{ x: 0, y: 1 }, { x: 1, y: 1 }] });
  assert.equal(strokeWalls(bare), null);
  const run = encodeStroke({ id: "r", t0: 1000, ch: "xyt", samples: [{ x: 0, y: 0, t: 500 }, { x: 1, y: 0, t: 520 }] });
  assert.deepEqual(strokeWalls(run), [1500, 1520], "a run the partial eraser cut keeps its times");
  assert.deepEqual(inkWallSpan(canvas([a, bare, run])), [1500, 5250]);
  assert.equal(inkWallSpan(canvas([bare])), null);
});

test("inkUntil on the wall clock: begun strokes drawn in order, the one in progress cut short, the rest pending", () => {
  const a = timed("a", 10_000, [[0, 0], [10, 100], [20, 200]]);
  const b = timed("b", 10_500, [[0, 0], [10, 100]], 10);
  const bare = encodeStroke({ id: "u", ch: "xy", samples: [{ x: 0, y: 1 }, { x: 1, y: 1 }] });
  const ink = canvas([b, a, bare]);   // the file's order is not the drawing order
  const before = inkUntil(ink, 9_000);
  assert.deepEqual(before.ink.strokes.map((s) => s.id), ["u"], "an untimed stroke is drawn as it is, under the rest");
  assert.deepEqual(before.pending, ["b", "a"]);
  assert.equal(before.latest, null);
  const mid = inkUntil(ink, 10_120);
  assert.deepEqual(mid.ink.strokes.map((s) => s.id), ["u", "a"]);
  assert.deepEqual(decodeStroke(mid.ink.strokes[1]).map((p) => p.x), [0, 10]);
  assert.deepEqual(mid.pending, ["b"]);
  assert.equal(mid.latest, "a");
  const later = inkUntil(ink, 10_550);
  assert.deepEqual(later.ink.strokes.map((s) => s.id), ["u", "a", "b"]);
  assert.equal(later.ink.strokes[1], a, "a complete stroke is the file's own object");
  assert.equal(later.latest, "b");
  const done = inkUntil(ink, 11_000);
  assert.equal(done.ink, ink, "once every stroke is complete the drawing is the file itself");
  assert.ok(done.done);
  assert.deepEqual(inkUntil(null, 0), { ink: null, pending: [], latest: null, done: true });
});

test("inkAtTime is inkUntil on the compressed clock's table", () => {
  const a = timed("a", 0, [[0, 0], [10, 100], [20, 200]]);
  const b = timed("b", 5_000, [[0, 0], [10, 100]], 10);
  const ink = canvas([a, b]);
  const line = inkTimeline(ink);
  for (const t of [-1, 0, 50, 199, 200, 201, 400, 650, 699, 700, 1000]) {
    assert.deepEqual(inkAtTime(ink, line, t).strokes, inkUntil(ink, t, line.times).ink.strokes, `t=${t}`);
  }
  assert.deepEqual(inkUntil(ink, 650, line.times).pending, [], "b begun at 600 on the compressed clock (200 + a 400 ms pause)");
});

test("compressedClock: a few seconds for an hour of writing, with the real moment readable at any t", () => {
  const strokes = Array.from({ length: 60 }, (_, i) => timed(`s${i}`, i * 60_000, [[0, 0], [1, 500]], i));
  const clock = compressedClock(canvas(strokes));
  assert.equal(clock.kind, "compressed");
  assert.equal(clock.duration, 15_000);
  assert.ok(clock.times instanceof Map && clock.times.size === 60);
  assert.equal(clock.toWall(0), 0);
  assert.equal(clock.toWall(clock.duration), 59 * 60_000 + 500);
  const t30 = clock.toT(30 * 60_000);
  assert.ok(t30 > 0 && t30 < clock.duration);
  assert.equal(clock.toWall(t30), 30 * 60_000);
});

// A lecture: a minute of audio, a 40 s pause, another minute, then a
// rollover with no pause and 30 s more.
const REC = {
  started: 1_000_000,
  segments: [
    { url: "/api/uploads/a.webm", started: 1_000_000, dur: 60_000, bytes: 1, mime: "audio/webm" },
    { url: "/api/uploads/b.webm", started: 1_100_000, dur: 60_000, bytes: 1, mime: "audio/webm" },
    { url: "/api/uploads/c.webm", started: 1_160_004, dur: 30_000, bytes: 1, mime: "audio/webm" },
  ],
};

test("recordingClock: the audio's own time is the segments end to end, the pause left out", () => {
  const clock = recordingClock(REC);
  assert.equal(clock.kind, "recording");
  assert.equal(clock.duration, 150_000);
  assert.deepEqual(clock.span, [1_000_000, 1_190_004]);
  assert.deepEqual(clock.segments.map((s) => s.start), [0, 60_000, 120_000]);
  assert.equal(clock.toT(1_000_000), 0);
  assert.equal(clock.toT(1_030_000), 30_000);
  assert.equal(clock.toT(1_060_000), 60_000, "the end of a segment");
  assert.equal(clock.toT(1_080_000), 60_000, "a moment in the pause is the resume point");
  assert.equal(clock.toT(1_100_000), 60_000);
  assert.equal(clock.toT(1_170_004), 130_000);
  assert.equal(clock.toT(999_999), null, "before the recording");
  assert.equal(clock.toT(1_190_005), null, "after it");
  assert.equal(clock.toWall(30_000), 1_030_000);
  assert.equal(clock.toWall(60_000), 1_100_000, "the join plays the second segment's first moment");
  assert.equal(clock.toWall(130_000), 1_170_004);
  assert.deepEqual(clock.segmentAt(130_000), { index: 2, segment: clock.segments[2], offset: 10_000 });
  assert.deepEqual(clock.segmentAt(-5).index, 0);
  assert.equal(clock.segmentAt(999_999_999).offset, 30_000, "past the end: the last segment's end");
  const empty = recordingClock({ started: 5, segments: [] });
  assert.equal(empty.duration, 0);
  assert.deepEqual(empty.span, [5, 5]);
  assert.equal(empty.toWall(0), null);
  assert.equal(empty.toT(5), null);
});

test("writingTicks: when something was written, on the recording's clock; the skips find the next one", () => {
  const clock = recordingClock(REC);
  const groups = [{ id: "g1", ink: canvas([
    timed("s1", 1_010_000, [[0, 0], [1, 300]]),          // 10 s in
    timed("s2", 1_080_000, [[0, 0], [1, 300]]),          // in the pause: the resume point
    timed("s3", 2_000_000, [[0, 0], [1, 300]]),          // after the recording: left out
    encodeStroke({ id: "u", ch: "xy", samples: [{ x: 0, y: 1 }, { x: 1, y: 1 }] }),
  ]) }];
  const blocks = [{ id: "n1", t0: 1_005_000 }, { id: "n2", t0: 1_170_004 }, { id: "n3", t0: 999 }, { id: "n4", t0: undefined }];
  const ticks = writingTicks(clock, groups, blocks);
  assert.deepEqual(ticks.map((k) => [k.kind, k.id, k.t]), [
    ["block", "n1", 5_000], ["ink", "s1", 10_000], ["ink", "s2", 60_000], ["block", "n2", 130_000]]);
  assert.equal(ticks[1].group, "g1");
  assert.equal(nextTick(ticks, 10_000, 1).id, "s2");
  assert.equal(nextTick(ticks, 10_200, -1).id, "n1", "a tick within the slack is not the previous one");
  assert.equal(nextTick(ticks, 130_000, 1), null);
  assert.equal(nextTick(ticks, 0, -1), null);
});

test("fmtClock", () => {
  assert.equal(fmtClock(0), "0:00");
  assert.equal(fmtClock(65_400), "1:05");
  assert.equal(fmtClock(3_600_000), "1:00:00");
  assert.equal(fmtClock(3_725_000), "1:02:05");
  assert.equal(fmtClock(-5), "0:00");
});
