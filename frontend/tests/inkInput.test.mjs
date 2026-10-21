import test from "node:test";
import assert from "node:assert/strict";
import { appendInkSample, penTilt, predictedInkSamples } from "../src/ink/inkInput.js";

const drawing = (k = 1) => ({ samples: [], pen: true, pressure: true, startTime: 100, k,
  use: { tool: "pen" }, toPt: (e) => ({ x: e.clientX / k, y: e.clientY / k }) });
const event = (x, timeStamp, pressure = 0.7) => ({ clientX: x, clientY: 20, timeStamp, pressure });

test("samples retain hardware timing, pressure changes and the pen-up endpoint", () => {
  const d = drawing();
  appendInkSample(d, event(10, 100, 0.2));
  appendInkSample(d, event(20, 104, 0.8));
  appendInkSample(d, event(20, 108, 0.4));
  appendInkSample(d, event(30, 112, 0), true);
  appendInkSample(d, event(30, 113, 0), true);
  assert.deepEqual(d.samples.map(({ x, p, t }) => [x, p, t]),
    [[10, 0.2, 0], [20, 0.8, 4], [20, 0.4, 8], [30, 0.4, 12]]);
});

test("pressure-off and finger strokes stay even; sample times cannot run backwards", () => {
  for (const patch of [{ pressure: false }, { pen: false }]) {
    const d = Object.assign(drawing(), patch);
    appendInkSample(d, event(10, 110, 0.1));
    appendInkSample(d, event(20, 105, 0.9));
    assert.deepEqual(d.samples.map(({ p, t }) => [p, t]), [[0.5, 10], [0.5, 10]]);
  }
});

test("prediction is bounded in time and screen distance and never changes stored samples", () => {
  for (const k of [0.5, 1, 3]) {
    const d = drawing(k);
    appendInkSample(d, event(10, 100));
    const move = { ...event(10, 100), getPredictedEvents: () => [event(15, 108), event(20, 116), event(21, 120)] };
    assert.equal(predictedInkSamples(d, move).length, 2);
    move.getPredictedEvents = () => [event(23, 108)];
    assert.deepEqual(predictedInkSamples(d, move), []);
    assert.equal(d.samples.length, 1);
    assert.deepEqual(predictedInkSamples(d, event(10, 100)), []);
    d.use.tool = "highlighter";
    assert.deepEqual(predictedInkSamples(d, move), []);
  }
});

// docs/dev/handwriting.md "Width": tilt is captured for pen pointers only,
// as integer degrees, and only when the pen reports one.
test("a pen's tilt is read as integer degrees from altitude / azimuth, else from tiltX / tiltY; upright or a mouse has none", () => {
  const rad = (deg) => deg * Math.PI / 180;
  assert.deepEqual(penTilt({ altitudeAngle: rad(40.4), azimuthAngle: rad(215.6) }), { a: 40, z: 216 });
  assert.deepEqual(penTilt({ altitudeAngle: rad(10), azimuthAngle: rad(359.8) }), { a: 10, z: 0 }, "the azimuth wraps at 360");
  assert.equal(penTilt({ altitudeAngle: Math.PI / 2, azimuthAngle: 0 }), null, "upright: no tilt to report");
  assert.equal(penTilt({ altitudeAngle: rad(89.6), azimuthAngle: 1 }), null, "an altitude that rounds to 90 is upright");
  // tiltX / tiltY, as the Pointer Events spec converts them
  assert.deepEqual(penTilt({ tiltX: 30, tiltY: 0 }), { a: 60, z: 0 });
  assert.deepEqual(penTilt({ tiltX: 0, tiltY: -45 }), { a: 45, z: 270 });
  assert.deepEqual(penTilt({ tiltX: -30, tiltY: 30 }), { a: 51, z: 135 });
  assert.deepEqual(penTilt({ tiltX: 90, tiltY: 0 }), { a: 0, z: 0 }, "flat on the page");
  assert.equal(penTilt({ tiltX: 0, tiltY: 0 }), null);
  assert.equal(penTilt({}), null);
  // a sample: a pen's carries a and z, the lift keeps the last contact's; a mouse's never does
  const d = drawing();
  const tilted = (x, timeStamp, deg) => ({ ...event(x, timeStamp), altitudeAngle: rad(deg), azimuthAngle: rad(90) });
  appendInkSample(d, tilted(10, 100, 50));
  appendInkSample(d, { ...event(20, 104), altitudeAngle: Math.PI / 2, azimuthAngle: 0 });
  appendInkSample(d, tilted(30, 108, 35));
  appendInkSample(d, { ...tilted(40, 112, 80), pressure: 0 }, true);
  assert.deepEqual(d.samples.map(({ a, z }) => [a, z]), [[50, 90], [undefined, undefined], [35, 90], [35, 90]]);
  const mouse = Object.assign(drawing(), { pen: false });
  appendInkSample(mouse, tilted(10, 100, 50));
  assert.equal("a" in mouse.samples[0], false);
});
