// node --test tests/selectionEdits.test.mjs (from frontend/) — the lasso
// selection's edits (ink/selectionEdits.js, useInkSelection.js's pure
// parts): the selected strokes, the pictures the lasso took and their half
// of an edit, what each action of the selection menu does and its history
// label, Duplicate, New note and Merge into note with their refusals,
// select all, Transcribe's groups and chips, the clipboard's shape and
// pill, and where a paste lands with its pictures.
import assert from "node:assert/strict";
import { test } from "node:test";
import { restyleBrush } from "../src/ink/finishStroke.js";
import {
  MAX_STROKES, boundsOf, encodeStroke, flipStrokes, newInk, restyleStrokes, strokeBounds, transformStrokes, unionBox,
} from "../src/ink/ink.js";
import {
  actionEdit, centredOn, clipOf, clipStatus, duplicatePlan, editPictures, inkChips, mergePlan, pasteShift, pastePictures, removePictures,
  selectAllItems, selectAllPictures, selectedPictures, selectedStrokes, selectionBox, selectionChanges, selectionStill, splitPlan,
  transcribeGroups, wholeGroup,
} from "../src/ink/selectionEdits.js";
import { reshapeInk } from "../src/ink/shapes.js";
import { mirrorPicture, movePicture, normalizePicture, picturesBySurface, transformPicture, turnedBox } from "../src/markup/picture.js";
import { findBlock } from "../src/shared/model/blockModel.js";
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

// A page's tree with the pictures a lasso may take: p1 turned, with a note
// under it; p2 locked; p3 naming no picture upload; and an ink group.
const PIC = (raw = {}) => ({ url: "/api/uploads/abc.png", x: 100, y: 100, w: 200, h: 100, ...raw });
const node = (id, properties = {}, children = []) => ({ id, content: "", properties, children });
const pictured = () => [node("p1", { picture: PIC({ angle: 90 }), pdf_page: 1 }, [node("p1note")]),
  node("p2", { picture: PIC({ locked: true }), pdf_page: 1 }), node("p3", { picture: { url: "/elsewhere.png" }, pdf_page: 1 }),
  node("g1", { ink_url: "/api/uploads/g1.ink" })];
const SURFACE = { width: 612, height: 792 };

test("the selection's pictures are those its surface still draws, none locked since; its box takes in their turned frames", () => {
  // p4 went to page 2 after the lasso took it (a collaborator, or its row moved)
  const tree = [...pictured(), node("p4", { picture: PIC(), pdf_page: 2 })], g1 = page(stroke("a", 400, 400)), inkOf = groupsOf({ g1 });
  const shown = picturesBySurface(tree).get(1);
  const sel = { page: 1, items: [{ id: "g1", ids: ["a"] }], pictures: ["p1", "p2", "p3", "p4", "gone"] };
  const p1 = normalizePicture(PIC({ angle: 90 }));
  assert.deepEqual(selectedPictures(sel, shown), [{ id: "p1", pic: p1 }], "p4 on another surface is not the selection's");
  assert.deepEqual(selectedPictures({ page: 1, items: [] }, shown), [], "a selection of strokes alone");
  assert.deepEqual(selectedPictures(sel, undefined), [], "a surface that draws no pictures");
  assert.deepEqual(selectionBox(sel, inkOf, shown), unionBox(boundsOf(g1, ["a"]), turnedBox(p1)));
  assert.deepEqual(selectionBox({ page: 1, items: [], pictures: ["p1"] }, inkOf, shown), turnedBox(p1), "pictures alone");
  assert.equal(selectionBox({ page: 1, items: [], pictures: ["p2"] }, inkOf, shown), null, "nothing left to hold");
  assert.equal(selectionBox({ page: 1, items: [], pictures: ["p4"] }, inkOf, shown), null, "nor in the box");
  // kept aside while a held E erased its strokes, it comes back with its pictures
  assert.deepEqual(selectionStill({ page: 1, items: [{ id: "g1", ids: ["erased"] }], pictures: ["p1"] }, inkOf),
    { page: 1, items: [], pictures: ["p1"] });
});

test("the pictures' half of an edit: each through the edit, a locked one as it is; Delete's takes the notes under them, Cut's leaves them", () => {
  const tree = pictured();
  const moved = editPictures(tree, ["p1", "p2"], (p) => movePicture(p, 10, 0, SURFACE));
  assert.equal(findBlock(moved, "p1").properties.picture.x, normalizePicture(PIC({ angle: 90 })).x + 10);
  assert.equal(findBlock(moved, "p2"), findBlock(tree, "p2"), "the locked one untouched");
  assert.equal(editPictures(tree, ["p3", "gone"], (p) => movePicture(p, 10, 0, SURFACE)), tree, "no picture that draws: the same tree");
  const kept = removePictures(tree, ["p1", "gone"]);
  assert.deepEqual(kept.map((b) => b.id), ["p2", "p3", "g1"]);
  assert.ok(!findBlock(kept, "p1note"), "the note under it went with it");
  assert.equal(removePictures(tree, ["gone"]), tree);
  const cut = removePictures(tree, ["p1", "gone"], true);
  assert.deepEqual(cut.map((b) => b.id), ["p1note", "p2", "p3", "g1"], "the note under it in its place");
  assert.equal(removePictures(tree, ["gone"], true), tree);
});

test("a turn, a resize and a flip carry the selection's pictures by the strokes' values; the other actions leave them", () => {
  const p = normalizePicture(PIC({ x: 300, y: 300 }));
  const value = { cx: 250, cy: 300, scale: 2, angle: 0 };
  const resize = actionEdit("transform", value, null).pictures;
  assert.equal(resize.label, "selection resize");
  assert.deepEqual(resize.each(p, SURFACE), transformPicture(p, value, SURFACE));
  assert.equal(actionEdit("transform", { ...value, angle: 0.5 }, null).pictures.label, "selection rotation");
  const flip = actionEdit("flip", { axis: "v" }, [0, 0, 100, 300]).pictures;
  assert.equal(flip.label, "selection flip");
  assert.deepEqual(flip.each(p, SURFACE), mirrorPicture(p, { cx: 50, cy: 150, axis: "v" }, SURFACE), "about the selection box's centre");
  for (const value of [{ color: "#ff0000" }, { size: 4 }, { dash: "dashed" }, { brush: "brush" }, { fill: null }]) {
    assert.equal(actionEdit("style", value, null).pictures, undefined, JSON.stringify(value));
  }
  assert.equal(actionEdit("reshape", { id: "a", params: {} }, null).pictures, undefined);
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
    check("flip", { axis: "v" }, "ink flip", flipStrokes(ink, ["a"], { cx: 70, cy: 15, axis: "v" }));
    const reshape = actionEdit("reshape", { id: "b", params: {} }, box);
    assert.equal(reshape.label, "ink shape edit");
    assert.equal(reshape.edit(ink, ["a"]), ink, "a shape handle of a stroke outside the selection changes nothing");
    // a shape handle's drag writes the selected shape again from its new params
    const line = { ...encodeStroke({ id: "L", ch: "xyt", pen: false, size: 2, samples: [{ x: 10, y: 10, t: 0 }, { x: 110, y: 10, t: 50 }] }),
      shape: { kind: "line", params: { x1: 10, y1: 10, x2: 110, y2: 10 } } };
    const drawn = page(stroke("a"), line), params = { x1: 10, y1: 10, x2: 210, y2: 60 };
    const reshaped = actionEdit("reshape", { id: "L", params }, box).edit(drawn, ["a", "L"]);
    assert.notEqual(reshaped, drawn, "the selected shape is written again");
    assert.deepEqual(reshaped, reshapeInk(drawn, "L", params));
    assert.deepEqual(reshaped.strokes[1].shape.params, params);
    for (const action of ["delete", "copy", "cut", "duplicate", "split", "merge", "transcribe", "redraw", "to-text", "copy-svg",
      "download-svg", "select-note", "show-note"]) {
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

test("Duplicate counts its copies against MAX_STROKES: a group that ends exactly at the cap takes them, one past it is refused", () => {
  const two = { page: 2, items: [{ id: "g", ids: ["a", "b"] }] };
  const atCap = page(...many(MAX_STROKES - 4), stroke("a"), stroke("b", 50));   // MAX_STROKES - 2, and 2 copies
  const plan = duplicatePlan(two, groupsOf({ g: atCap }), { dx: 12, dy: 12 });
  assert.equal(plan.changes[0].after.strokes.length, MAX_STROKES);
  const pastCap = page(...many(MAX_STROKES - 3), stroke("a"), stroke("b", 50));   // MAX_STROKES - 1, and 2 copies
  assert.deepEqual(duplicatePlan(two, groupsOf({ g: pastCap }), { dx: 12, dy: 12 }),
    { refused: "This handwriting note is full. Start a new note before duplicating." });
});

test("New note takes the selected strokes into a new group, ids kept; the whole of one note alone is refused", () => {
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
  // one note whole and part of another: a new note of all of them
  const g3 = page(stroke("y"), stroke("z", 200));
  const mixed = splitPlan({ page: 1, items: [{ id: "g1", ids: ["a", "b"] }, { id: "g3", ids: ["y"] }] }, groupsOf({ g1, g3 }), () => "new");
  assert.deepEqual(mixed.changes.map((c) => [c.id, ids(c.after)]), [["g1", []], ["g3", ["z"]], ["new", ["a", "b", "y"]]]);
  assert.deepEqual(mixed.items, [{ id: "new", ids: ["a", "b", "y"] }]);
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
  assert.equal(mergePlan(whole("g3"), inkOf, tree, surfaces).changes[0].id, "g2", "the last group: into the one just before it, not the first");
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
  const atCap = { ...groups, g1: page(...many(MAX_STROKES - 2)) };
  assert.equal(mergePlan(whole("g2"), groupsOf(atCap), tree, surfaces).changes[0].after.strokes.length, MAX_STROKES,
    "a merge that ends exactly at the cap goes through");
});

test("Merge into note leaves a group with a comment thread as it is: never merged away, never merged into", () => {
  const groups = { g1: page(stroke("a")), g2: page(stroke("b", 50)), g3: page(stroke("d", 200)) };
  const inkOf = groupsOf(groups);
  const surfaces = new Map([[1, [{ id: "g1" }, { id: "g2" }, { id: "g3" }]]]);
  const tree = ["g1", "g2", "g3"].map((id) => ({ id, content: "", properties: {}, children: [] }));
  const whole = (id) => ({ page: 1, items: [{ id, ids: ids(groups[id]) }] });
  const threaded = (id) => id === "g2";
  assert.deepEqual(mergePlan(whole("g2"), inkOf, tree, surfaces, threaded), { refused: "Not merged: this handwriting has comments." });
  assert.equal(mergePlan(whole("g3"), inkOf, tree, surfaces, threaded).changes[0].id, "g1", "the one before it has a thread: the next one before");
  assert.equal(mergePlan(whole("g1"), inkOf, tree, surfaces, threaded).changes[0].id, "g3", "the first after it with none");
  assert.equal(mergePlan(whole("g1"), inkOf, tree, surfaces, (id) => id !== "g1"), null, "every other group has one");
  assert.equal(mergePlan(whole("g2"), inkOf, tree, surfaces).changes[0].id, "g1", "no threads by default");
});

test("wholeGroup: the one group a selection takes every stroke of, else null", () => {
  const inkOf = groupsOf({ g1: page(stroke("a"), stroke("b", 50)), g2: page(stroke("c")), g0: page() });
  assert.equal(wholeGroup({ page: 1, items: [{ id: "g1", ids: ["b", "a"] }] }, inkOf), "g1");
  assert.equal(wholeGroup({ page: 1, items: [{ id: "g1", ids: ["a"] }] }, inkOf), null, "part of it");
  assert.equal(wholeGroup({ page: 1, items: [{ id: "g1", ids: ["a", "x"] }] }, inkOf), null, "a stroke it no longer holds");
  assert.equal(wholeGroup({ page: 1, items: [{ id: "g1", ids: ["a", "b"] }, { id: "g2", ids: ["c"] }] }, inkOf), null, "two groups");
  assert.equal(wholeGroup({ page: 1, items: [{ id: "g0", ids: [] }] }, inkOf), null, "an empty group");
  assert.equal(wholeGroup({ page: 1, items: [], pictures: ["p"] }, inkOf), null, "pictures alone");
  assert.equal(wholeGroup(null, inkOf), null);
});

test("select all takes every stroke of the surface's groups, a group with none left out", () => {
  const inkOf = groupsOf({ g1: page(stroke("a"), stroke("b", 50)), g2: page(), g4: page(stroke("d")) });
  assert.deepEqual(selectAllItems([{ id: "g1" }, { id: "g2" }, { id: "g3" }, { id: "g4" }], inkOf), [{ id: "g1", ids: ["a", "b"] }, { id: "g4", ids: ["d"] }]);
  assert.deepEqual(selectAllItems(undefined, inkOf), [], "a surface with no groups");
});

test("select all takes the surface's pictures too, never a locked one nor one that names no upload", () => {
  const tree = [...pictured(), node("p4", { picture: PIC({ z: -2 }), pdf_page: 1 }), node("p5", { picture: PIC(), pdf_page: 2 })];
  assert.deepEqual(selectAllPictures(picturesBySurface(tree).get(1)), ["p4", "p1"], "as they stack, p2 locked and p3 no picture left out");
  assert.deepEqual(selectAllPictures(undefined), [], "a surface with no pictures");
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
  assert.equal(transcribeGroups({ page: 1, items: [{ id: "g1", ids: ["a", "b", "erased"] }] }, inkOf).whole, false,
    "every stroke of g1 and one erased since: not whole either");
  assert.equal(transcribeGroups({ page: 1, items: [{ id: "gone", ids: ["z"] }] }, inkOf).whole, false, "no group");
  const chips = inkChips(part.groups, "page-1");
  assert.deepEqual(chips, [
    { kind: "ink", id: "g1", page_id: "page-1", space: g1.space, strokes: [g1.strokes[0]], text: "Selected handwriting · 1 stroke" },
    { kind: "ink", id: "g2", page_id: "page-1", space: g2.space, strokes: [g2.strokes[0]], text: "Selected handwriting · 1 stroke" },
  ]);
  assert.equal(inkChips(whole.groups, "p")[0].text, "Selected handwriting · 2 strokes");
  assert.deepEqual(inkChips(transcribeGroups({ page: 1, items: [{ id: "g1", ids: ["gone"] }] }, inkOf).groups, "p"), [], "no chip without strokes");
});

test("the handwriting clipboard holds the file's space, the box in points, the strokes as they are and the pictures with their captions", () => {
  const g1 = { ...newInk(2, 612, 792), strokes: [stroke("a", 400, 400), stroke("b")] }, inkOf = groupsOf({ g1 });
  const strokesOnly = { page: 2, items: [{ id: "g1", ids: ["a"] }] }, [ax0, ay0, ax1, ay1] = boundsOf(g1, ["a"]);
  assert.deepEqual(clipOf(strokesOnly, inkOf, undefined),
    { type: "gamma/ink", version: 2, space: g1.space, box: { x1: ax0, y1: ay0, x2: ax1, y2: ay1 }, strokes: [g1.strokes[0]] });
  assert.equal(clipOf(strokesOnly, inkOf, undefined).strokes[0], g1.strokes[0], "the stroke object itself");
  // p1 turned, with a caption and a note under it; p2 locked
  const tree = pictured().map((b) => (b.id === "p1" ? { ...b, content: "The **figure**" } : b));
  const shown = picturesBySurface(tree).get(1), p1 = normalizePicture(PIC({ angle: 90 }));
  const both = clipOf({ page: 1, items: [{ id: "g1", ids: ["a"] }], pictures: ["p1", "p2"] }, inkOf, shown);
  const [x0, y0] = turnedBox(p1);
  assert.deepEqual(both.box, { x1: x0, y1: y0, x2: ax1, y2: ay1 }, "the box takes in the turned frame");
  assert.deepEqual(both.pictures, [{ picture: p1, caption: "The **figure**" }], "the locked one left behind, the notes under p1 too");
  const alone = clipOf({ page: 1, items: [], pictures: ["p1"] }, inkOf, shown);
  const [, , x1, y1] = turnedBox(p1);
  assert.deepEqual([alone.space, alone.strokes, alone.box], [null, [], { x1: x0, y1: y0, x2: x1, y2: y1 }],
    "pictures alone: no space, no strokes");
  assert.equal(clipOf({ page: 1, items: [{ id: "g1", ids: ["gone"] }], pictures: ["p2"] }, inkOf, shown), null, "nothing left to hold");
});

test("a copy's and a cut's pill say what they took", () => {
  const clip = (n, m) => ({ strokes: Array(n).fill({}), ...(m ? { pictures: Array(m).fill({}) } : {}) });
  assert.equal(clipStatus(clip(3, 0), false), "Copied 3 strokes.");
  assert.equal(clipStatus(clip(1, 0), true), "Cut 1 stroke.");
  assert.equal(clipStatus(clip(0, 1), false), "Copied 1 picture.");
  assert.equal(clipStatus(clip(2, 2), true), "Cut 2 strokes and 2 pictures.");
});

test("a paste's pictures: new blocks naming the same upload, shifted with the strokes, kept on the surface, over the surface's pictures in the order they stacked", () => {
  const low = normalizePicture(PIC({ x: 10, y: 10, z: -3 })), high = normalizePicture(PIC({ x: 50, y: 50, crop: { x: 0.5, y: 0, w: 0.5, h: 1 }, opacity: 0.5 }));
  const clip = { strokes: [], pictures: [{ picture: low, caption: "low" }, { picture: high, caption: "" }] };
  // the PDF page being pasted onto has a picture at z 4
  const tree = [node("top", { picture: PIC({ z: 4 }), pdf_page: 3 })];
  const out = pastePictures(tree, clip, 3, ["c1", "c2"], { dx: 100, dy: 200 }, SURFACE, 7);
  assert.equal(out[0], tree[0], "the rest kept");
  const [c1, c2] = [findBlock(out, "c1"), findBlock(out, "c2")];
  assert.deepEqual(c1.properties, { t0: 7, picture: { ...movePicture(low, 100, 200, SURFACE), z: 4 }, pdf_page: 3 });
  assert.equal(c1.content, "low", "its caption with it");
  assert.deepEqual(c2.properties.picture, { ...movePicture(high, 100, 200, SURFACE), z: 4 }, "its crop and opacity kept");
  assert.equal(c2.properties.picture.url, PIC().url, "the same upload");
  assert.deepEqual(picturesBySurface(out).get(3).map((b) => b.id), ["top", "c1", "c2"], "over the page's, in the order they stacked");
  const edge = pastePictures([], clip, 3, ["c1", "c2"], { dx: 1000, dy: 0 }, SURFACE, 7);
  assert.equal(findBlock(edge, "c1").properties.picture.x, SURFACE.width - low.w, "kept on the surface");
  const sheet = pastePictures([node("sheet", { sheet: { w: 612, h: 792 } })], clip, "sheet", ["c1", "c2"], { dx: 0, dy: 0 }, SURFACE, 7);
  assert.deepEqual(findBlock(sheet, "sheet").children.map((c) => c.id), ["c1", "c2"], "on a sheet, its children");
  assert.equal(findBlock(sheet, "c1").properties.pdf_page, undefined);
  assert.equal(findBlock(sheet, "c1").properties.picture.z, undefined, "on a surface with none above 0, their own z left behind");
  assert.equal(pastePictures(tree, { strokes: [] }, 3, [], { dx: 0, dy: 0 }, SURFACE, 7), tree, "no pictures: the same tree");
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
