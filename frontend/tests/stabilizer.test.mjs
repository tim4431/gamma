// node --test tests/  (from frontend/) — the lazy brush behind a preset's
// `stabilize`: the string's radius per level, the cusp rule, the catch-up
// at the lift, and that pressure and time ride through untouched.
import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_STABILIZE, STABILIZE_RADII, makeStabilizer } from "../src/ink/stabilizer.js";

const S = (x, y, t = 0, p = 0.5) => ({ x, y, p, t });
const near = (a, b, eps = 1e-9) => Math.abs(a - b) < eps;

test("level 0 and unknown levels pass every sample through as it is", () => {
  for (const level of [0, -1, 7, undefined, "x"]) {
    const st = makeStabilizer(level, { k: 1 });
    const s = S(10, 10, 5);
    assert.equal(st.push(s), s);
    assert.deepEqual(st.finish(S(20, 10, 9)), [S(20, 10, 9)]);
  }
  assert.deepEqual(STABILIZE_RADII, [0, 3, 6, 10, 16]);
  assert.equal(MAX_STABILIZE, 4);
});

test("the string's radius is 3, 6, 10 or 16 css px, divided by k to points; the brush trails the pointer by it", () => {
  for (const [level, R] of [[1, 3], [2, 6], [3, 10], [4, 16]]) {
    for (const k of [1, 2, 0.5]) {
      const st = makeStabilizer(level, { k });
      const r = R / k;
      assert.deepEqual(st.push(S(0, 0, 0)), S(0, 0, 0), "the first sample starts the brush and is emitted");
      assert.equal(st.push(S(r * 0.9, 0, 10)), null, "within the radius nothing moves");
      const out = st.push(S(r + 4, 0, 20, 0.8));
      assert.ok(near(out.x, 4) && out.y === 0, `level ${level} k ${k}: the brush moved by the distance beyond R (${out.x})`);
      assert.equal(out.p, 0.8); assert.equal(out.t, 20);
      const diag = st.push(S(r + 4 + r * Math.SQRT1_2 + 3, r * Math.SQRT1_2 + 3, 30));
      assert.ok(near(Math.hypot(r + 4 + r * Math.SQRT1_2 + 3 - diag.x, r * Math.SQRT1_2 + 3 - diag.y), r, 1e-9), "the pointer stays exactly R from the brush after a pull");
    }
  }
});

test("pressure, time, tilt and any other key ride through untouched; only x and y are the brush's", () => {
  const st = makeStabilizer(2, { k: 1 });
  st.push({ x: 0, y: 0, p: 0.1, t: 0, a: 40, z: 10 });
  const out = st.push({ x: 20, y: 0, p: 0.9, t: 16, a: 45, z: 12, extra: "kept" });
  assert.deepEqual(out, { x: 14, y: 0, p: 0.9, t: 16, a: 45, z: 12, extra: "kept" });
});

test("the cusp rule: a reversal of more than 90° jumps the brush to where the pointer turned, so a corner stays a corner", () => {
  const st = makeStabilizer(3, { k: 1 });   // R = 10
  st.push(S(0, 0, 0));
  st.push(S(30, 0, 10));                      // brush at 20
  st.push(S(40, 0, 20));                      // brush at 30, the string taut
  const cusp = st.push(S(40, 10, 30));        // the pointer turns 90°: not a cusp (not over 90°)
  assert.ok(cusp === null || cusp.x !== 40, "a right angle is pulled round, not jumped");
  const st2 = makeStabilizer(3, { k: 1 });
  st2.push(S(0, 0, 0)); st2.push(S(30, 0, 10)); st2.push(S(40, 0, 20));
  const jump = st2.push(S(35, 2, 30, 0.7));   // back the way it came: over 90° against the brush's move
  assert.deepEqual(jump, S(40, 0, 30, 0.7), "the previous pointer position is emitted, with the new sample's p and t");
  assert.equal(st2.push(S(33, 2, 40)), null, "and the brush rests there until the string is taut again");
  const next = st2.push(S(20, 0, 50));
  assert.ok(near(next.x, 30) && near(next.y, 0), `the brush follows back (${next.x}, ${next.y})`);
  // a back-step under R / 2 is tremor, not a cusp: the brush waits; the pointer passing the tip again forgets it
  const st3 = makeStabilizer(3, { k: 1 });
  st3.push(S(0, 0, 0)); st3.push(S(30, 0, 10));                 // brush at 20, taut
  assert.equal(st3.push(S(29, 0, 20)), null, "1 pt back: tremor");
  assert.equal(st3.push(S(28.5, 0, 30)), null, "1.5 pt back: still waiting");
  assert.deepEqual(st3.push(S(31, 0, 40)), S(21, 0, 40), "past the tip again: an ordinary pull, the tip forgotten");
  assert.deepEqual(st3.push(S(24, 0, 50)), S(31, 0, 50), "7 pt back (over R / 2 = 5): the tip is emitted");
  // a shake across the stroke is not a cusp: the retreat is measured along the brush's move, and a
  // pointer more than R from the tip sideways drops it, so a zigzag is smoothed, not copied
  const st5 = makeStabilizer(3, { k: 1 });
  st5.push(S(0, 0, 0)); st5.push(S(30, 0, 10));                 // brush at 20, taut, moved +x
  assert.equal(st5.push(S(28, 9, 20)), null, "2 pt back and 9 across: a tremor so far");
  const across = st5.push(S(27, 14, 30));                        // 3 pt back, 14 across: off the tip's reach
  assert.ok(across && !(across.x === 30 && across.y === 0), "no jump to the tip");
  assert.ok(near(Math.hypot(27 - across.x, 14 - across.y), 10), "the string pulls on from where the brush was");
  // a lift with a cusp pending emits the tip first, then the catch-up
  const st4 = makeStabilizer(3, { k: 1 });
  st4.push(S(0, 0, 0)); st4.push(S(30, 0, 10));
  assert.equal(st4.push(S(28, 0, 20)), null);
  const lift = st4.finish(S(27, 0, 30));
  assert.deepEqual([lift[0].x, lift[0].y], [30, 0], "the tip");
  assert.deepEqual(lift.at(-1), S(27, 0, 30));
  assert.ok(lift.every((s, i) => i === 0 || s.t > lift[i - 1].t) && lift[0].t > 10, "t climbs from the last emitted t");
});

test("finish catches the line up to the lift point: at most 8 samples about R / 2 apart, p the lift's, t spread, the lift last", () => {
  const st = makeStabilizer(2, { k: 1 });   // R = 6
  st.push(S(0, 0, 0, 0.4));
  st.push(S(20, 0, 100, 0.6));               // brush at 14, t 100
  const tail = st.finish(S(30, 0, 160, 0.3));
  assert.equal(tail.length, Math.ceil(16 / 3), "16 pt at R / 2 = 3 → 6 samples");
  assert.deepEqual(tail.at(-1), S(30, 0, 160, 0.3), "the lift point exactly");
  for (let i = 0; i < tail.length; i++) {
    assert.equal(tail[i].p, 0.3);
    assert.ok(i === 0 || tail[i].t > tail[i - 1].t, "t climbs");
    assert.ok(tail[i].t > 100 && tail[i].t <= 160, `t between the last emitted and the lift (${tail[i].t})`);
    assert.ok(i === 0 || tail[i].x > tail[i - 1].x, "x climbs toward the lift");
  }
  const long = makeStabilizer(1, { k: 1 });  // R = 3
  long.push(S(0, 0, 0)); long.push(S(10, 0, 10));
  assert.equal(long.finish(S(200, 0, 20)).length, 8, "never more than 8 catch-up samples");
  // the control point is the brush plus half its last move: a turn before the lift curves the catch-up
  const bend = makeStabilizer(3, { k: 1 });
  bend.push(S(0, 0, 0)); bend.push(S(30, 0, 10));  // brush at 20, moved +x
  const curve = bend.finish(S(20, 30, 20));
  assert.ok(curve[0].x > 20 && curve[0].y > 0, "the first catch-up sample keeps the brush's heading before turning");
  assert.deepEqual(curve.at(-1), S(20, 30, 20));
  const idle = makeStabilizer(2, { k: 1 });
  assert.deepEqual(idle.finish(S(5, 5, 1)), [S(5, 5, 1)], "a lift before any push is the lift alone");
  const still = makeStabilizer(2, { k: 1 });
  still.push(S(5, 5, 0));
  assert.deepEqual(still.finish(S(5, 5, 9)), [S(5, 5, 9)], "nothing to catch up");
});

test("a shake across the stroke's direction comes out calm: the drawn line's spread is a fraction of the pointer's", () => {
  // the spread of the line's middle (the start and the lift are the pen's own points)
  const check = (level, pts, what) => {
    const R = STABILIZE_RADII[level];
    const st = makeStabilizer(level, { k: 1 });
    const out = [];
    pts.forEach((s, i) => { if (i === pts.length - 1) out.push(...st.finish(s)); else { const q = st.push(s); if (q) out.push(q); } });
    const mid = (a) => a.filter((s) => s.x >= pts[0].x + 2 * R && s.x <= pts.at(-1).x - 2 * R);
    const spread = (a) => Math.max(...a.map((s) => s.y)) - Math.min(...a.map((s) => s.y));
    assert.ok(spread(mid(out)) <= 0.5 * spread(mid(pts)),
      `level ${level}, ${what}: drawn spread ${spread(mid(out)).toFixed(2)} (pointer ${spread(mid(pts)).toFixed(2)})`);
    assert.ok(out.length >= 4 && out.at(-1).x === pts.at(-1).x, "the line reaches the lift");
  };
  // a square-wave zigzag, one step per sample
  for (const [level, dx, amp] of [[4, 4, 5], [3, 3, 4], [2, 2, 2.5], [1, 1, 1.2]]) {
    const pts = [];
    for (let i = 0; i <= 60; i++) pts.push(S(100 + i * dx, 300 + (i % 2 ? amp : -amp), i * 8));
    check(level, pts, `zigzag ±${amp} every ${dx} px`);
  }
  // a 10 Hz tremor sampled at 120 Hz with the pen moving 100 px/s
  for (const [level, a] of [[1, 2], [2, 4], [3, 4], [4, 6]]) {
    const pts = [];
    for (let i = 0; i <= 240; i++) pts.push(S(100 + i * 0.83, 300 + a * Math.sin(2 * Math.PI * 10 * i / 120), Math.round(i * 8.33)));
    check(level, pts, `10 Hz tremor ±${a} px`);
  }
});
