// The gesture rules shared by mouse and finger (shared/ui/press.js): what
// counts as a double tap, the stand-in event a held finger gives a menu
// opener, the props a call site spreads, which press or click opens a menu
// button's menu, when a dialog's backdrop closes it, and when a pen held
// down is plainly up without its lift.
import assert from "node:assert/strict";
import { test } from "node:test";
import { backdropPress, doublePress, isDoubleTap, menuPress, penLifted, pressAt, triggerPress } from "../src/shared/ui/press.js";

const el = {}, other = {};
const tapAt = (t, x = 100, y = 100, on = el) => ({ el: on, t, x, y });

test("two releases on one element, close in time and place, are a double tap", () => {
  assert.equal(isDoubleTap(tapAt(0), tapAt(250)), true);
  assert.equal(isDoubleTap(tapAt(0), tapAt(250, 120, 110)), true, "a finger lands a little off");
  assert.equal(isDoubleTap(null, tapAt(250)), false, "the first release");
  assert.equal(isDoubleTap(tapAt(0), tapAt(450)), false, "too slow");
  assert.equal(isDoubleTap(tapAt(0), tapAt(250, 140, 100)), false, "too far apart");
  assert.equal(isDoubleTap(tapAt(0), tapAt(250, 100, 100, other)), false, "another element");
});

test("a finger's second release runs the handler once; the browser's own dblclick after it is ignored", () => {
  let runs = 0;
  const props = doublePress(() => { runs++; });
  const up = (t, pointerType = "touch") => props.onPointerUp({ pointerType, currentTarget: el, timeStamp: t, clientX: 5, clientY: 5 });
  up(1000); assert.equal(runs, 0);
  up(1200); assert.equal(runs, 1);
  props.onDoubleClick({ timeStamp: 1210 }); assert.equal(runs, 1, "the echo of the taps just counted");
  up(1300); assert.equal(runs, 1, "a third release starts over");
  props.onDoubleClick({ timeStamp: 5000 }); assert.equal(runs, 2, "a mouse's double-click");
  up(6000, "mouse"); up(6100, "mouse"); assert.equal(runs, 2, "a mouse's releases are the browser's to count");
});

test("the stand-in press carries what a menu opener reads", () => {
  const e = pressAt(12, 34, el);
  assert.deepEqual([e.clientX, e.clientY, e.currentTarget], [12, 34, el]);
  e.preventDefault(); e.stopPropagation();
});

test("no handler, no props", () => {
  assert.deepEqual(menuPress(undefined), {});
  assert.deepEqual(doublePress(null), {});
  assert.equal(menuPress(() => {})["data-press"], "menu");
});

test("a menu button: a pointer's press and a click no press led to run it; the click after a press does not", () => {
  const ran = [];
  const props = triggerPress((e) => ran.push(e.what));
  props.onPointerDown({ button: 0, pointerType: "mouse", what: "mouse" });
  props.onClick({ detail: 1, what: "the mouse's click" });
  props.onPointerDown({ button: 0, pointerType: "touch", what: "finger" });
  props.onPointerDown({ button: 0, pointerType: "pen", what: "pen" });
  props.onPointerDown({ button: 2, pointerType: "mouse", what: "right button" });
  props.onPointerDown({ button: 1, pointerType: "mouse", what: "middle button" });
  props.onClick({ detail: 0, what: "Enter" });
  assert.deepEqual(ran, ["mouse", "finger", "pen", "Enter"]);
});

test("one release is counted once, by the innermost element it reaches", () => {
  let inner = 0, outer = 0;
  const grip = doublePress(() => { inner++; }), frame = doublePress(() => { outer++; });
  const release = (t) => {
    const nativeEvent = {};
    const at = (currentTarget) => ({ pointerType: "touch", currentTarget, timeStamp: t, clientX: 5, clientY: 5, nativeEvent });
    grip.onPointerUp(at(el)); frame.onPointerUp(at(other)); // bubbling: the grip, then the frame around it
  };
  release(20000); release(20200);
  assert.deepEqual([inner, outer], [1, 0]);
});

test("a backdrop closes its dialog only for a press that starts and ends on it", () => {
  // Settings' backdrop (outer) holds a sub-dialog's backdrop (sub); a press
  // bubbles through the sub-dialog's handlers, then the outer ones.
  const outerEl = {}, subEl = {}, field = {};
  const closed = [];
  const outer = backdropPress(() => closed.push("outer")), sub = backdropPress(() => closed.push("sub"));
  const press = (down, up, clickAt, on = [outer, outerEl]) => {
    for (const props of [sub, outer]) props.onPointerDown({ target: down });
    for (const props of [sub, outer]) props.onPointerUp({ target: up });
    on[0].onClick({ target: clickAt, currentTarget: on[1] });
  };
  press(outerEl, outerEl, outerEl); assert.deepEqual(closed, ["outer"], "a click on the backdrop");
  press(field, outerEl, outerEl); assert.deepEqual(closed, ["outer"], "text selected in a field, let go past the dialog's edge");
  press(outerEl, field, outerEl); assert.deepEqual(closed, ["outer"], "a press from the backdrop let go inside the dialog");
  press(field, field, field); assert.deepEqual(closed, ["outer"], "a click inside the dialog");
  press(subEl, subEl, subEl, [sub, subEl]); assert.deepEqual(closed, ["outer", "sub"], "the sub-dialog's own backdrop, through the outer handlers");
});

test("a pen held down is up without its lift once it hovers or goes down again; nothing else says so", () => {
  const pen = (type, buttons, pointerId = 7) => ({ type, pointerType: "pen", pointerId, buttons });
  assert.equal(penLifted(pen("pointermove", 0, 1), 7), true, "the Pencil hovering (WebKit's hover pointer)");
  assert.equal(penLifted(pen("pointermove", 0), 7), true, "hovering as the same pointer");
  assert.equal(penLifted(pen("pointerdown", 1, 8), 7), true, "a new contact");
  assert.equal(penLifted(pen("pointerdown", 1), 7), true, "a desktop pen down again under its one id");
  assert.equal(penLifted(pen("pointermove", 1), 7), false, "the pen moving in contact");
  assert.equal(penLifted(pen("pointermove", 32), 7), false, "its eraser end in contact");
  assert.equal(penLifted(pen("pointerup", 0, 8), 7), false, "another pointer's lift");
  assert.equal(penLifted({ type: "pointerdown", pointerType: "touch", pointerId: 3, buttons: 1 }, 7), false, "a finger (a palm)");
  assert.equal(penLifted({ type: "pointermove", pointerType: "mouse", pointerId: 1, buttons: 0 }, 7), false, "a mouse");
  assert.equal(penLifted(pen("pointermove", 0), null), false, "no pen held");
});
