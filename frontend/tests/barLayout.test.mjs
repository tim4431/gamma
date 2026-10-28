// A bar's layout (shared/model/barLayout.js): the order and modes codec,
// the Settings edits, and where each item goes — on a bar of its own here,
// so the top bar's and the strip's registries can change without these.
import test from "node:test";
import assert from "node:assert/strict";
import { BAR_MODES, barShows, defineBar, moveVisible } from "../src/shared/model/barLayout.js";

const BAR = defineBar([
  { id: "a", popover: "pa", modes: ["always", "hidden"] },
  { id: "b", popover: "pb", modes: ["always", "auto", "hidden"], labels: { auto: "When busy" }, tips: { auto: "While busy" } },
  { id: "c", modes: ["auto", "always", "hidden"], phone: false, labels: { always: "Everywhere" }, tips: { hidden: "Its own place" } },
  { id: "d", modes: ["always", "hidden"] },
], { tips: { hidden: "In the menu" } });

const ORDER = ["a", "b", "c", "d"];
const MODES = { a: "always", b: "always", c: "auto", d: "always" };
const ids = (list) => list.map((x) => x.id);

test("the modes, and whether the bar shows each", () => {
  assert.deepEqual(BAR_MODES, ["always", "auto", "hidden"]);
  assert.equal(barShows("always"), true);
  assert.equal(barShows("auto"), false);
  assert.equal(barShows("auto", { active: true }), true);
  assert.equal(barShows("auto", { open: true }), true, "an open popover keeps its button");
  assert.equal(barShows("hidden", { active: true, open: true }), false);
  assert.equal(barShows("bogus", { active: true }), false);
});

test("moveVisible: the visible slots, the others keeping their places, the same list for a no-op", () => {
  const list = ["a", "x", "b", "y", "c"];
  const shown = (id) => !"xy".includes(id);
  assert.deepEqual(moveVisible(list, "a", 3, shown), ["x", "b", "y", "c", "a"], "last");
  assert.deepEqual(moveVisible(list, "a", 2, shown), ["x", "b", "y", "a", "c"], "before c, after the hidden y");
  assert.deepEqual(moveVisible(list, "c", 0, shown), ["c", "a", "x", "b", "y"], "first");
  assert.deepEqual(moveVisible(list, "c", 1, shown), ["a", "x", "c", "b", "y"], "before b, the hidden x staying after a");
  assert.deepEqual(moveVisible(list, "b", 0, shown), ["b", "a", "x", "y", "c"]);
  assert.deepEqual(moveVisible(list, "b", 3, shown), ["a", "x", "y", "c", "b"]);
  for (const to of [0, 1]) assert.equal(moveVisible(list, "a", to, shown), list, `a to slot ${to} is where it stands`);
  for (const to of [1, 2]) assert.equal(moveVisible(list, "b", to, shown), list, `b to slot ${to} is where it stands`);
  assert.equal(moveVisible(list, "x", 0, shown), list, "an invisible element is not moved");
  assert.equal(moveVisible(list, "z", 0, shown), list, "nor one the list lacks");
  assert.deepEqual(moveVisible(list, "a", 9, shown), ["x", "b", "y", "c", "a"], "a slot past the end is last");

  // Elements compared as themselves: objects, as the ink presets are.
  const tools = [{ id: 1 }, { id: 2, hidden: true }, { id: 3 }];
  const moved = moveVisible(tools, tools[2], 0, (x) => !x.hidden);
  assert.deepEqual(moved.map((x) => x.id), [3, 1, 2]);
  assert.equal(moved[0], tools[2]);
});

test("the default is the registry's order and each item's first mode", () => {
  assert.deepEqual(BAR.DEFAULT, { order: ORDER, modes: MODES });
  assert.ok(Object.isFrozen(BAR.DEFAULT) && Object.isFrozen(BAR.DEFAULT.order) && Object.isFrozen(BAR.DEFAULT.modes));
  assert.equal(BAR.items.length, 4);
  assert.equal(BAR.item("c").phone, false);
  assert.equal(BAR.item("ghost"), null);
  assert.ok(Object.isFrozen(BAR));
});

test("normalize: anything but an object is the codec's default, an object comes back whole", () => {
  for (const bad of [null, undefined, "always", 3, true, ["a", "b"]]) assert.equal(BAR.normalize(bad), undefined);
  assert.deepEqual(BAR.normalize({}), { order: ORDER, modes: MODES });
  assert.deepEqual(BAR.normalize({ order: "a,b", modes: "always" }), { order: ORDER, modes: MODES }, "parts of the wrong kind");
  assert.deepEqual(BAR.normalize({ a: "hidden", c: "always" }), { order: ORDER, modes: MODES }, "a map of modes alone is no layout");
  assert.deepEqual(BAR.normalize({ order: [], modes: { b: "auto" } }), { order: ORDER, modes: { ...MODES, b: "auto" } });
});

test("normalize: the stored order deduplicated, unknown ids dropped", () => {
  assert.deepEqual(BAR.normalize({ order: ["d", "c", "b", "a"] }).order, ["d", "c", "b", "a"]);
  assert.deepEqual(BAR.normalize({ order: ["d", "ghost", "c", "d", 7, null, "b", "a", "c"] }).order, ["d", "c", "b", "a"]);
});

test("normalize: an item the order lacks goes after its registry predecessor, at the start when it is the first", () => {
  assert.deepEqual(BAR.normalize({ order: ["d", "a", "c"] }).order, ["d", "a", "b", "c"], "b after a");
  assert.deepEqual(BAR.normalize({ order: ["c", "a"] }).order, ["c", "d", "a", "b"], "b after a, d after c");
  assert.deepEqual(BAR.normalize({ order: ["d", "c", "b"] }).order, ["a", "d", "c", "b"], "a at the start");
  assert.deepEqual(BAR.normalize({ order: ["d", "a"] }).order, ["d", "a", "b", "c"], "b and c in a run after a");
  assert.deepEqual(BAR.normalize({ order: ["ghost"] }).order, ORDER);
});

test("normalize: a mode its item does not offer takes the item's default", () => {
  const { modes } = BAR.normalize({ order: ORDER, modes: { a: "auto", b: "hidden", c: "bogus", d: null, ghost: "hidden" } });
  assert.deepEqual(modes, { ...MODES, b: "hidden" }, "a offers no auto");
});

test("read and isDefault: no value is the default, the order matters", () => {
  assert.equal(BAR.read(undefined), BAR.DEFAULT);
  assert.equal(BAR.read("junk"), BAR.DEFAULT);
  assert.deepEqual(BAR.read({ modes: { d: "hidden" } }), { order: ORDER, modes: { ...MODES, d: "hidden" } });
  assert.ok(BAR.isDefault(undefined));
  assert.ok(BAR.isDefault(BAR.DEFAULT));
  assert.ok(BAR.isDefault({ order: ["a", "ghost", "b"], modes: { ghost: "hidden" } }));
  assert.ok(!BAR.isDefault({ order: ["b", "a"] }), "another order");
  assert.ok(!BAR.isDefault({ modes: { b: "auto" } }), "another mode");
});

test("setMode: a new layout with that mode, an unoffered one leaving it as it is", () => {
  const value = { order: ["d", "c", "b", "a"], modes: { b: "auto" } };
  const next = BAR.setMode(value, "a", "hidden");
  assert.deepEqual(next, { order: ["d", "c", "b", "a"], modes: { ...MODES, b: "auto", a: "hidden" } });
  assert.deepEqual(value, { order: ["d", "c", "b", "a"], modes: { b: "auto" } }, "the value given is not changed");
  assert.deepEqual(BAR.setMode(undefined, "c", "always").modes, { ...MODES, c: "always" });
  assert.deepEqual(BAR.setMode(value, "a", "auto"), BAR.read(value), "a offers no auto");
  assert.deepEqual(BAR.setMode(value, "ghost", "hidden"), BAR.read(value));
  assert.deepEqual(BAR.DEFAULT, { order: ORDER, modes: MODES }, "the default is never written");
});

test("move: to a slot among the items shown, the others keeping their places", () => {
  assert.deepEqual(BAR.move(undefined, "d", 0).order, ["d", "a", "b", "c"]);
  assert.deepEqual(BAR.move(undefined, "a", 4).order, ["b", "c", "d", "a"]);
  assert.deepEqual(BAR.move(undefined, "a", 2).order, ["b", "a", "c", "d"]);
  const modes = { b: "hidden" };
  assert.deepEqual(BAR.move({ modes }, "c", 0).modes, { ...MODES, b: "hidden" }, "the modes come along");

  // A list that leaves b out (its row does not apply here): a, c, d.
  const noB = (item) => item.id !== "b";
  assert.deepEqual(BAR.move(undefined, "d", 1, noB).order, ["a", "b", "d", "c"], "before c; b stays after a");
  assert.deepEqual(BAR.move(undefined, "a", 1, noB).order, ORDER, "a to slot 1 is where it stands");
  assert.deepEqual(BAR.move(undefined, "a", 2, noB).order, ["b", "c", "a", "d"]);
  assert.deepEqual(BAR.move(undefined, "b", 0, noB), BAR.DEFAULT, "an item the list leaves out is not moved");
  assert.deepEqual(BAR.move(undefined, "ghost", 0), BAR.DEFAULT);
  assert.deepEqual(BAR.move(undefined, "c", 2), BAR.DEFAULT, "a no-op");
});

test("place: the layout's order, the modes, the phone, a guide, an open popover, the active and the pinned", () => {
  const at = (list) => Object.fromEntries(list.map((x) => [x.id, x.at]));
  const all = (where) => Object.fromEntries(ORDER.map((id) => [id, where]));

  const plain = BAR.place(undefined);
  assert.deepEqual(ids(plain), ORDER);
  assert.equal(plain[2].item, BAR.item("c"));
  assert.deepEqual(at(plain), { ...all("bar"), c: "menu" }, "c is auto, with nothing to say");
  assert.deepEqual(at(BAR.place(undefined, { active: { c: true } })), all("bar"));
  assert.deepEqual(ids(BAR.place({ order: ["d", "b"] })), ["a", "d", "b", "c"], "the layout's order");

  const quiet = { modes: { a: "hidden", b: "auto", c: "hidden" } };
  assert.deepEqual(at(BAR.place(quiet)), { a: "menu", b: "menu", c: "menu", d: "bar" });
  assert.equal(at(BAR.place(quiet, { active: { b: true } })).b, "bar");
  assert.equal(at(BAR.place(quiet, { open: "pb" })).b, "bar", "its popover is open");
  assert.equal(at(BAR.place(quiet, { open: "pa" })).a, "menu", "a hidden item's popover opens off the bar");
  assert.equal(at(BAR.place(quiet, { active: { c: true } })).c, "menu", "hidden, whatever it has to say");
  assert.equal(at(BAR.place({ modes: { d: "auto" } }, { open: null })).d, "bar", "d offers no auto: it keeps its default");
  assert.equal(at(BAR.place({ modes: { c: "auto" } }, { open: undefined })).c, "menu", "no open popover matches an item without one");

  assert.deepEqual(at(BAR.place(quiet, { pinned: { a: true, c: true } })), { a: "bar", b: "menu", c: "bar", d: "bar" }, "in use, on the bar");
  assert.deepEqual(at(BAR.place(quiet, { guide: true })), all("bar"), "a tour or hint shows every item");

  assert.deepEqual(at(BAR.place(undefined, { phone: true, active: { c: true } })), { ...all("bar"), c: "menu" }, "no room on the phone's bar");
  assert.equal(at(BAR.place(undefined, { phone: true, guide: true, pinned: { c: true } })).c, "menu", "whatever else");
  assert.equal(at(BAR.place(quiet, { phone: true })).a, "menu", "the phone's bar follows the preference");
});

test("modeLabel and modeTip: the item's own word first, then the bar's", () => {
  const [a, b, c] = BAR.items;
  assert.equal(BAR.modeLabel(a, "always"), "Shown");
  assert.equal(BAR.modeLabel(a, "hidden"), "Hidden");
  assert.equal(BAR.modeLabel(b, "auto"), "When busy");
  assert.equal(BAR.modeLabel(c, "always"), "Everywhere");
  assert.equal(BAR.modeLabel(c, "auto"), "auto", "no word anywhere: the mode itself");
  assert.equal(BAR.modeTip(a, "hidden"), "In the menu");
  assert.equal(BAR.modeTip(c, "hidden"), "Its own place");
  assert.equal(BAR.modeTip(b, "auto"), "While busy");
  assert.equal(BAR.modeTip(a, "always"), "");
  assert.equal(defineBar([{ id: "x", modes: ["always", "hidden"] }]).modeTip({ id: "x" }, "hidden"), "", "a bar with no tips");
});
