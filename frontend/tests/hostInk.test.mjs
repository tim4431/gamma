// node --test tests/  (from frontend/) — the iPad shell's side of a stroke
// (src/ink/hostInk.js, docs/dev/ipad.md "The pen"): the claim's tool in the
// preset's look, the host's samples as pointer events, and a shape between
// page points and the host's css px.
import assert from "node:assert/strict";
import { test } from "node:test";
import { claimTool, hostEvent, shapeFromHost, shapeToHost } from "../src/ink/hostInk.js";
import { penTilt, penTwist } from "../src/ink/inkInput.js";
import { toolStyle } from "../src/ink/ink.js";

test("claimTool: a plain pen claims the four keys an older shell reads, nothing else", () => {
  const look = { ...toolStyle({ id: "p", kind: "pen", color: "#1f4fd8", size: 2 }), pen: true };
  assert.deepEqual(claimTool(look, { k: 1.5 }), { kind: "pen", color: "#1f4fd8", size: 3, opacity: 1 });
});

test("claimTool: the look's keys, each only away from its default", () => {
  const brush = { ...toolStyle({ id: "p", kind: "pen", color: "#1f4fd8", size: 2, brush: "brush", dash: "dashed", stabilize: 2 }), pen: false };
  assert.deepEqual(claimTool(brush, { k: 1.5 }),
    { kind: "pen", color: "#1f4fd8", size: 3, opacity: 1, brush: "brush", pressure: false, dash: [6, 6], stabilize: 2 },
    "the dash in css px: two sizes on, two off, of the width on screen");
  const dotted = claimTool({ ...toolStyle({ id: "p", kind: "pen", color: "#000000", size: 4, brush: "pencil", dash: "dotted" }), pen: true }, { k: 2 });
  assert.equal(dotted.brush, "pencil");
  assert.equal(dotted.size, 8);
  assert.ok(Math.abs(dotted.dash[0] - 0.8) < 1e-9 && dotted.dash[1] === 16, `dotted: a tenth of a size on, two off (${dotted.dash})`);
  assert.equal("pressure" in dotted, false, "pressure read: the key left out");
  assert.equal(claimTool({ ...toolStyle({ id: "p", kind: "pen", color: "#000000", size: 2, brush: "monoline" }), pen: true }).brush, "monoline");
  assert.equal("brush" in claimTool({ tool: "pen", color: "#000000", size: 2, brush: "fountain", pen: true }), false,
    "a brush the build does not know is the plain Pen");
});

test("claimTool: a highlighter multiplies; smoothing outside 1–4 is left out", () => {
  const hl = { ...toolStyle({ id: "h", kind: "highlighter", color: "#fde047", size: 14, straight: "off" }), pen: true };
  const tool = claimTool(hl, { k: 1 });
  assert.deepEqual(tool, { kind: "highlighter", color: "#fde047", size: 14, opacity: hl.opacity, blend: "multiply" });
  assert.equal("brush" in claimTool({ ...hl, brush: "pencil" }), false, "a highlighter has no brush");
  assert.equal("stabilize" in claimTool({ tool: "pen", color: "#000000", size: 2, stabilize: 0 }), false);
  assert.equal("stabilize" in claimTool({ tool: "pen", color: "#000000", size: 2, stabilize: 7 }), false);
  assert.equal(claimTool({ tool: "pen", color: "#000000", size: 2, stabilize: 4 }).stabilize, 4);
});

test("hostEvent: four values carry no tilt; six carry it in radians, which penTilt rounds back to degrees", () => {
  assert.deepEqual(hostEvent([10.5, 20.25, 0.4, 16]), { clientX: 10.5, clientY: 20.25, pressure: 0.4, timeStamp: 16 });
  assert.equal(penTilt(hostEvent([10, 20, 0.4, 16])), null);
  const ev = hostEvent([10, 20, 0.4, 16, 39.6, 120.4]);
  assert.ok(Math.abs(ev.altitudeAngle - 39.6 * Math.PI / 180) < 1e-12 && Math.abs(ev.azimuthAngle - 120.4 * Math.PI / 180) < 1e-12);
  assert.deepEqual(penTilt(ev), { a: 40, z: 120 });
  assert.equal(penTilt(hostEvent([10, 20, 0.4, 16, 89.7, 30])), null, "an upright pencil reports no tilt, as a browser's");
  assert.deepEqual(penTilt(hostEvent([10, 20, 0.4, 16, 30, 359.8])), { a: 30, z: 0 }, "the azimuth wraps at 360");
});

test("shapeToHost / shapeFromHost: points moved and scaled, lengths scaled, angles kept, on the 1/100 grid", () => {
  const at = { left: 40, top: 100, k: 1.5 };
  assert.deepEqual(shapeToHost({ kind: "line", params: { x1: 10, y1: 20, x2: 110, y2: 20.333 } }, at),
    { kind: "line", params: { x1: 55, y1: 130, x2: 205, y2: 130.5 } });
  assert.deepEqual(shapeToHost({ kind: "rect", params: { cx: 100, cy: 200, w: 40, h: 20, angle: 12.5 } }, at),
    { kind: "rect", params: { cx: 190, cy: 400, w: 60, h: 30, angle: 12.5 } });
  assert.deepEqual(shapeToHost({ kind: "circle", params: { cx: 10, cy: 10, r: 5 } }, at), { kind: "circle", params: { cx: 55, cy: 115, r: 7.5 } });
  assert.deepEqual(shapeToHost({ kind: "polygon", params: { cx: 0, cy: 0, r: 10, n: 6, angle: 7 } }, at).params,
    { cx: 40, cy: 100, r: 15, n: 6, angle: 7 }, "a side count is a count");
  assert.deepEqual(shapeToHost({ kind: "arrow", params: { x1: 0, y1: 0, x2: 100, y2: 0, head: 20 } }, at).params,
    { x1: 40, y1: 100, x2: 190, y2: 100, head: 30 });
  for (const shape of [
    { kind: "triangle", params: { x1: 10, y1: 10, x2: 50, y2: 10, x3: 30, y3: 40 } },
    { kind: "ellipse", params: { cx: 300, cy: 400, rx: 120, ry: 60, angle: 30 } },
    { kind: "rect", params: { cx: 100, cy: 200, w: 40, h: 20, angle: -12.5 } },
  ]) assert.deepEqual(shapeFromHost(shapeToHost(shape, at), at), shape, `${shape.kind} there and back`);
  assert.deepEqual(shapeFromHost({ kind: "line", params: { x1: 55, y1: 130, x2: 205.01, y2: 130 } }, at).params,
    { x1: 10, y1: 20, x2: 110.01, y2: 20 });
  assert.equal(shapeToHost({ kind: "blob", params: {} }, at), undefined, "a kind it does not know");
});

test("claimTool: a calligraphy pen claims its nib, 0..179, and leaves it to the roll unless the preset locks it", () => {
  const cal = { ...toolStyle({ id: "c", kind: "pen", color: "#000000", size: 2, brush: "calligraphy", nib: -30 }), pen: true };
  // its smoothing of 2 rides too: a calligraphy pen's default, which its preset leaves out (ink.js presetSmoothing)
  assert.deepEqual(claimTool(cal, { k: 1 }), { kind: "pen", color: "#000000", size: 2, opacity: 1, brush: "calligraphy", nib: 150, nibRoll: true,
    stabilize: 2 });
  assert.equal("stabilize" in claimTool({ ...toolStyle({ id: "c", kind: "pen", color: "#000000", size: 2, brush: "calligraphy", stabilize: 0 }), pen: true }),
    false, "a calligraphy pen with Smoothing off claims none");
  const locked = { ...toolStyle({ id: "c", kind: "pen", color: "#000000", size: 2, brush: "calligraphy", nibLock: true }), pen: true };
  assert.deepEqual([claimTool(locked).nib, "nibRoll" in claimTool(locked)], [45, false], "45 to start; locked: no roll");
  assert.equal("nib" in claimTool({ ...toolStyle({ id: "p", kind: "pen", color: "#000000", size: 2 }), nib: 30, pen: true }), false,
    "only a calligraphy pen has a nib");
});

test("hostEvent: a seventh value is the roll, as twist, which penTwist reads (clockwise degrees 0..359)", () => {
  const ev = hostEvent([10, 20, 0.4, 16, 40, 120, 30.4]);
  assert.equal(ev.twist, 30.4);
  assert.equal(penTwist(ev), 30);
  assert.equal(penTwist(hostEvent([10, 20, 0.4, 16, 40, 120, 359.7])), 0, "wraps at 360");
  assert.equal(penTwist(hostEvent([10, 20, 0.4, 16, 40, 120])), null, "six values: no roll");
  assert.equal("twist" in hostEvent([10, 20, 0.4, 16]), false);
  assert.equal(penTwist({ pointerType: "mouse", twist: 0 }), null, "a mouse has no roll");
  assert.equal(penTwist({ pointerType: "touch", twist: 0 }), null);
  assert.equal(penTwist({ pointerType: "pen", twist: 90 }), 90);
  assert.equal(penTwist({ pointerType: "pen" }), null);
});
