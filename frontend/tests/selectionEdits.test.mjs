// node --test tests/selectionEdits.test.mjs (from frontend/) — the lasso
// selection's edits (ink/selectionEdits.js, useInkSelection.js's pure
// parts): the selected strokes, what each action of the selection menu
// does and its history label, Duplicate, New note and Merge into note with
// their refusals, select all, Transcribe's groups and chips, the
// clipboard's shape, and where a paste lands.
import assert from "node:assert/strict";
import { test } from "node:test";
import { restyleBrush } from "../src/ink/finishStroke.js";
import {
  MAX_STROKES, boundsOf, encodeStroke, flipStrokes, newInk, restyleStrokes, strokeBounds, transformStrokes, unionBox,
} from "../src/ink/ink.js";
import {
  actionEdit, centredOn, clipOf, duplicatePlan, inkChips, mergePlan, pasteShift, selectAllItems, selectedStrokes, selectionChanges,
  selectionStill, splitPlan, transcribeGroups,
} from "../src/ink/selectionEdits.js";
import { setLocale } from "../src/shared/i18n/i18n.js";

const stroke = (id, x = 10, y = 10) => encodeStroke({ id, ch: "xy", pen: false, size: 1, samples: [{ x, y }, { x: x + 20, y: y + 10 }] });
const page = (...strokes) => ({ ...newInk(1, 612, 792), strokes });
const ids = (ink) => ink.strokes.map((s) => s.id);
const many = (n, prefix = "s") => Array.from({ length: n }, (_, i) => ({ id: `${prefix}${i}`, ch: "xy", pts: [0, 0] }));
// a page's groups by id, and the inkOf that reads them (null for one it does not know)
const groupsOf = (groups) => (id) => groups[id] || null;

test("the selected strokes in selection order, their union box and the first group's space", () => {
  const g1 = page(stroke("a"), stroke("b", 100), stroke("c", 200)), g2 = { ...newInk(1, 600, 800), strokes: [stroke("x", 300, 300)] };
  const inkOf = groupsOf({ g1, g2, g3: page(stroke("q")) });
  const sel = { page: 1, items: [{ id: "gone", ids: ["z"] }, { id: "g3", ids: ["erased"] }, { id: "g1", ids: ["c", "a"] }, { id: "g2", ids: ["x"] }] };
  const got = selectedStrokes(sel, inkOf);
  assert.deepEqual(got.strokes.map((s) => s.id), ["a", "c", "x"], "each group's in its own order, the groups in the selection's");
  assert.equal(got.strokes[0], g1.strokes[0], "the strokes as the groups hold them");
  assert.deepEqual(got.box, unionBox(boundsOf(g1, ["a", "c"]), strokeBounds(g2.strokes[0])));
  assert.equal(got.space, g1.space, "the first group with selected strokes gives the space");
  assert.equal(selectedStrokes({ page: 1, items: [{ id: "g3", ids: ["erased"] }] }, inkOf), null);
  assert.equal(selectedStrokes(null, inkOf), null);
});

test("a selection kept aside comes back without the strokes erased meanwhile, or not at all", () => {
  const inkOf = groupsOf({ g1: page(stroke("a"), stroke("c", 50)), g2: page(stroke("y")) });
  const sel = { page: 2, items: [{ id: "g1", ids: ["a", "b", "c"] }, { id: "g2", ids: ["x"] }, { id: "gone", ids: ["z"] }] };
  assert.deepEqual(selectionStill(sel, inkOf), { page: 2, items: [{ id: "g1", ids: ["a", "c"] }] });
  assert.equal(selectionStill({ page: 2, items: [{ id: "g2", ids: ["x"] }] }, inkOf), null);
  assert.equal(selectionStill(null, inkOf), null);
});

test("an edit of the selection is one change per group it changed", () => {
  const g1 = page(stroke("a"), stroke("b", 50)), g2 = page(stroke("x"));
  const inkOf = groupsOf({ g1, g2 });
  const sel = { page: 3, items: [{ id: "g1", ids: ["a"] }, { id: "g2", ids: ["x"] }, { id: "gone", ids: ["z"] }] };
  const edit = (ink, list) => (list.includes("a") ? { ...ink, strokes: ink.strokes.filter((s) => !list.includes(s.id)) } : ink);
  const changes = selectionChanges(sel, inkOf, edit);
  assert.deepEqual(changes.map((c) => [c.id, c.page, ids(c.before), ids(c.after)]), [["g1", 3, ["a", "b"], ["b"]]], "g2 came back the same");
  assert.equal(changes[0].before, g1);
});

test("each in-place action's edit, and its history label in T() (translated when the pill says it) or t() as it always was", () => {
  const labels = ["ink style change", "ink line change", "ink fill change", "ink shape edit", "ink color change", "ink width change",
    "ink rotation", "ink resize", "ink flip"];
  setLocale("xx", Object.fromEntries(labels.map((l) => [l, l.toUpperCase()])));
  try {
    const ink = page(stroke("a"), stroke("b", 100));
    const box = [10, 10, 130, 20];
    const check = (action, value, label, expected) => {
      const got = actionEdit(action, value, box);
      assert.equal(got.label, label, `${action} ${JSON.stringify(value)}`);
      assert.deepEqual(got.edit(ink, ["a"]), expected, `${action} ${JSON.stringify(value)}: the edit`);
    };
    check("style", { brush: "brush" }, "ink style change", restyleBrush(ink, ["a"], "brush"));
    check("style", { brush: null }, "ink style change", restyleBrush(ink, ["a"], null));
    check("style", { dash: "dashed" }, "ink line change", restyleStrokes(ink, ["a"], { dash: "dashed" }));
    check("style", { fill: "#ff0000" }, "ink fill change", restyleStrokes(ink, ["a"], { fill: "#ff0000" }));
    check("style", { color: "#ff0000" }, "INK COLOR CHANGE", restyleStrokes(ink, ["a"], { color: "#ff0000" }));
    check("style", { size: 4 }, "INK WIDTH CHANGE", restyleStrokes(ink, ["a"], { size: 4 }));
    check("transform", { cx: 50, cy: 50, angle: 0.5 }, "INK ROTATION", transformStrokes(ink, ["a"], { cx: 50, cy: 50, angle: 0.5 }));
    check("transform", { cx: 50, cy: 50, scale: 2 }, "INK RESIZE", transformStrokes(ink, ["a"], { cx: 50, cy: 50, scale: 2 }));
    check("flip", { axis: "h" }, "ink flip", flipStrokes(ink, ["a"], { cx: 70, cy: 15, axis: "h" }));
    const reshape = actionEdit("reshape", { id: "b", params: {} }, box);
    assert.equal(reshape.label, "ink shape edit");
    assert.equal(reshape.edit(ink, ["a"]), ink, "a shape handle of a stroke outside the selection changes nothing");
    for (const action of ["delete", "copy", "cut", "duplicate", "split", "merge", "transcribe", "select-note", "show-note"]) {
      assert.equal(actionEdit(action, {}, box), null, action);
    }
    assert.equal(actionEdit("flip", { axis: "v" }, null), null, "a flip with nothing selected");
  } finally {
    setLocale("en", {});
  }
});

test("Duplicate puts the copies beside the originals in their groups, and is refused at MAX_STROKES before any change", () => {
  const g1 = page(stroke("a"), stroke("b", 100)), g2 = page(stroke("x"));
  const inkOf = groupsOf({ g1, g2, g3: page() });
  const plan = duplicatePlan({ page: 2, items: [{ id: "g1", ids: ["a"] }, { id: "g3", ids: ["gone"] }, { id: "g2", ids: ["x"] }] }, inkOf, { dx: 12, dy: 12 });
  assert.deepEqual(plan.changes.map((c) => [c.id, c.page, c.before === inkOf(c.id), c.after.strokes.length]), [["g1", 2, true, 3], ["g2", 2, true, 2]]);
  assert.deepEqual(plan.items.map((item) => item.id), ["g1", "g2"]);
  assert.deepEqual(plan.items[0].ids, [plan.changes[0].after.strokes[2].id], "the copies, to select");
  assert.deepEqual(strokeBounds(plan.changes[0].after.strokes[2]), strokeBounds(g1.strokes[0]).map((v) => v + 12), "12 pt away");
  const full = page(...many(MAX_STROKES - 1), stroke("a"), stroke("b", 50));
  const refused = duplicatePlan({ page: 2, items: [{ id: "g2", ids: ["x"] }, { id: "full", ids: ["a", "b"] }] }, groupsOf({ g2, full }), { dx: 12, dy: 12 });
  assert.deepEqual(refused, { refused: "This handwriting note is full. Start a new note before duplicating." }, "no change, g2's included");
});

test("New note takes the selected strokes into a new group, ids kept; the whole of one note is refused", () => {
  const g1 = page(stroke("a"), stroke("b", 100)), g2 = page(stroke("x"));
  const inkOf = groupsOf({ g1, g2 });
  const plan = splitPlan({ page: 1, items: [{ id: "g1", ids: ["b"] }, { id: "g2", ids: ["x"] }] }, inkOf, () => "new");
  assert.deepEqual(plan.changes.map((c) => [c.id, ids(c.before), ids(c.after)]), [["g1", ["a", "b"], ["a"]], ["g2", ["x"], []], ["new", [], ["b", "x"]]]);
  assert.equal(plan.changes[2].after.space, g1.space, "on the first group's surface");
  assert.deepEqual(plan.items, [{ id: "new", ids: ["b", "x"] }]);
  let made = 0;
  assert.deepEqual(splitPlan({ page: 1, items: [{ id: "g1", ids: ["a", "b"] }] }, inkOf, () => { made++; return "new"; }),
    { refused: "This handwriting is already a note of its own." });
  assert.equal(made, 0, "no id is made for a refusal");
  assert.equal(splitPlan({ page: 1, items: [{ id: "g1", ids: ["gone"] }] }, inkOf, () => "new"), null, "nothing selected any more");
});

test("Merge into note appends a whole group to the previous one on its surface, else the next, and empties it", () => {
  const groups = { g1: page(stroke("a")), g2: page(stroke("b", 50), stroke("c", 90)), g3: page(stroke("d", 200)), g0: page() };
  const inkOf = groupsOf(groups);
  const surfaces = new Map([[1, [{ id: "g0" }, { id: "g1" }, { id: "g2" }, { id: "g3" }]]]);
  const block = (id, over = {}) => ({ id, content: "", properties: {}, children: [], ...over });
  const tree = ["g0", "g1", "g2", "g3"].map((id) => block(id));
  const whole = (id) => ({ page: 1, items: [{ id, ids: ids(groups[id]) }] });
  const plan = mergePlan(whole("g2"), inkOf, tree, surfaces);
  assert.deepEqual(plan.changes.map((c) => [c.id, ids(c.before), ids(c.after)]), [["g1", ["a"], ["a", "b", "c"]], ["g2", ["b", "c"], []]]);
  assert.deepEqual(plan.items, [{ id: "g1", ids: ["b", "c"] }]);
  assert.equal(mergePlan(whole("g1"), inkOf, tree, surfaces).changes[0].id, "g2", "the first group: into the next one (g0 has no strokes)");
  assert.equal(mergePlan({ page: 1, items: [{ id: "g2", ids: ["b"] }] }, inkOf, tree, surfaces), null, "part of a group");
  assert.equal(mergePlan({ page: 1, items: [{ id: "g1", ids: ["a"] }, { id: "g3", ids: ["d"] }] }, inkOf, tree, surfaces), null, "two groups");
  assert.equal(mergePlan(whole("g1"), inkOf, tree, new Map([[1, [{ id: "g1" }]]])), null, "no other group on the surface");
  const refusal = { refused: "Not merged: this note has a caption or notes under it." };
  assert.deepEqual(mergePlan(whole("g2"), inkOf, [block("g2", { content: "a caption" })], surfaces), refusal);
  assert.deepEqual(mergePlan(whole("g2"), inkOf, [block("g2", { children: [block("n")] })], surfaces), refusal);
  assert.notDeepEqual(mergePlan(whole("g2"), inkOf, [block("g2", { content: "  " })], surfaces), refusal, "a blank caption is none");
  const full = { ...groups, g1: page(...many(MAX_STROKES - 1)) };
  assert.deepEqual(mergePlan(whole("g2"), groupsOf(full), tree, surfaces),
    { refused: "This handwriting note is full. Move part of it to a new note, or start a new one." });
});

test("select all takes every stroke of the surface's groups, a group with none left out", () => {
  const inkOf = groupsOf({ g1: page(stroke("a"), stroke("b", 50)), g2: page(), g4: page(stroke("d")) });
  assert.deepEqual(selectAllItems([{ id: "g1" }, { id: "g2" }, { id: "g3" }, { id: "g4" }], inkOf), [{ id: "g1", ids: ["a", "b"] }, { id: "g4", ids: ["d"] }]);
  assert.deepEqual(selectAllItems(undefined, inkOf), [], "a surface with no groups");
});

test("Transcribe: whole groups go as their blocks; part of one, as one chip per group of the selected strokes", () => {
  const g1 = page(stroke("a"), stroke("b", 50)), g2 = page(stroke("x"));
  const inkOf = groupsOf({ g1, g2 });
  const wholeSel = { page: 1, items: [{ id: "g1", ids: ["b", "a"] }, { id: "g2", ids: ["x"] }, { id: "gone", ids: ["z"] }] };
  const whole = transcribeGroups(wholeSel, inkOf);
  assert.equal(whole.whole, true, "every stroke of each group, in any order; a group gone is left out");
  assert.deepEqual(whole.groups.map((g) => g.id), ["g1", "g2"]);
  const part = transcribeGroups({ page: 1, items: [{ id: "g1", ids: ["a", "gone"] }, { id: "g2", ids: ["x"] }] }, inkOf);
  assert.equal(part.whole, false, "part of g1 (a stroke erased since counts against it)");
  assert.equal(transcribeGroups({ page: 1, items: [{ id: "gone", ids: ["z"] }] }, inkOf).whole, false, "no group");
  const chips = inkChips(part.groups, "page-1");
  assert.deepEqual(chips, [
    { kind: "ink", id: "g1", page_id: "page-1", space: g1.space, strokes: [g1.strokes[0]], text: "Selected handwriting · 1 stroke" },
    { kind: "ink", id: "g2", page_id: "page-1", space: g2.space, strokes: [g2.strokes[0]], text: "Selected handwriting · 1 stroke" },
  ]);
  assert.equal(inkChips(whole.groups, "p")[0].text, "Selected handwriting · 2 strokes");
  assert.deepEqual(inkChips(transcribeGroups({ page: 1, items: [{ id: "g1", ids: ["gone"] }] }, inkOf).groups, "p"), [], "no chip without strokes");
});

test("the handwriting clipboard holds the file's space, the box in points and the strokes as they are", () => {
  const strokes = [stroke("a")], space = newInk(2, 612, 792).space;
  assert.deepEqual(clipOf({ strokes, box: [10, 20, 110, 70], space }),
    { type: "gamma/ink", version: 2, space, box: { x1: 10, y1: 20, x2: 110, y2: 70 }, strokes });
  assert.equal(clipOf({ strokes, box: [0, 0, 1, 1], space }).strokes, strokes);
});

test("a paste puts the box's top-left at the point, kept on the surface; on a surface too small, its top-left corner stays on", () => {
  const clip = { box: { x1: 100, y1: 100, x2: 300, y2: 200 } };
  const size = { width: 612, height: 792 };
  assert.deepEqual(pasteShift(clip, { x: 50, y: 60 }, size), { dx: -50, dy: -40 });
  assert.deepEqual(pasteShift(clip, { x: 500, y: 700 }, size), { dx: 312, dy: 592 }, "the box kept inside: its right and bottom edges on the edges");
  assert.deepEqual(pasteShift(clip, { x: -30, y: -30 }, size), { dx: -100, dy: -100 }, "nor past the left and top");
  assert.deepEqual(pasteShift(clip, { x: 40, y: 40 }, { width: 150, height: 80 }), { dx: -100, dy: -100 }, "too small: the top-left at 0, 0");
});

test("centred: the box's top-left that puts the box in the middle of the surface", () => {
  const clip = { box: { x1: 100, y1: 100, x2: 300, y2: 200 } };
  assert.deepEqual(centredOn(clip, { width: 612, height: 792 }), { x: 206, y: 346 });
  assert.deepEqual(centredOn(clip, { width: 100, height: 50 }), { x: -50, y: -25 }, "larger than the surface: the paste's clamp takes it from there");
});
