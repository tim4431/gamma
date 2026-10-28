// The topbar's items (app/topbarItems.js): the preference's shape, the
// Settings rows' words, and where each item goes. The layout's own rules
// (the order's codec, moves, pinning) are tests/barLayout.test.mjs's.
import test from "node:test";
import assert from "node:assert/strict";
import { PREFS } from "../src/app/prefDefs.js";
import { TOPBAR, TOPBAR_ITEMS } from "../src/app/topbarItems.js";
import { BAR_MODES } from "../src/shared/model/barLayout.js";

const ORDER = ["add", "tasks", "search", "share", "sync", "inbox", "view"];
const MODES = { add: "always", tasks: "always", search: "always", share: "always", sync: "auto", inbox: "always", view: "always" };

test("every item offers known modes, auto only with its own word, and has the parts its row and anchor need", () => {
  assert.equal(TOPBAR.items, TOPBAR_ITEMS);
  const ids = new Set();
  const popovers = new Set();
  for (const item of TOPBAR_ITEMS) {
    assert.ok(!ids.has(item.id), `${item.id} is listed twice`);
    ids.add(item.id);
    assert.ok(item.popover && !popovers.has(item.popover), `${item.id} has a popover name of its own`);
    popovers.add(item.popover);
    assert.ok(item.label && item.hint && item.keywords, `${item.id} has a label, a hint and synonyms`);
    assert.ok(item.modes.length >= 2, `${item.id} offers a choice`);
    assert.ok(item.modes.includes("hidden"), `${item.id} can be hidden`);
    for (const mode of item.modes) {
      assert.ok(BAR_MODES.includes(mode), `${item.id}: unknown mode ${mode}`);
      assert.ok(TOPBAR.modeLabel(item, mode) !== mode, `${item.id}: the ${mode} choice has a word`);
    }
    if (item.modes.includes("auto")) assert.ok(item.labels?.auto, `${item.id} names its auto`);
    assert.equal(TOPBAR.modeTip(item, "hidden"), "A row of the account menu instead (More, on a phone)",
      `${item.id}: the hidden choice says where the item goes`);
  }
});

test("the default is the bar's order and each item's first mode, and the preference stores it", () => {
  assert.deepEqual(TOPBAR.DEFAULT, { order: ORDER, modes: MODES });
  for (const item of TOPBAR_ITEMS) assert.equal(TOPBAR.DEFAULT.modes[item.id], item.modes[0]);
  assert.equal(PREFS.topbar.key, "gamma-topbar");
  assert.equal(PREFS.topbar.scope, "account");
  assert.equal(PREFS.topbar.default, TOPBAR.DEFAULT);
  const value = TOPBAR.setMode(TOPBAR.move(undefined, "search", 0), "inbox", "auto");
  assert.deepEqual(PREFS.topbar.parse(PREFS.topbar.serialize(value)),
    { order: ["search", "add", "tasks", "share", "sync", "inbox", "view"], modes: { ...MODES, inbox: "auto" } });
  assert.equal(PREFS.topbar.parse("not json"), undefined);
  assert.equal(PREFS.topbar.parse(JSON.stringify(["add"])), undefined);
  assert.equal(PREFS.syncPillScope, undefined, "the old sync pill preference is gone");
});

test("a stored value comes back whole: unknown ids dropped, missing or unoffered modes take the default", () => {
  assert.deepEqual(TOPBAR.normalize({}), TOPBAR.DEFAULT);
  assert.deepEqual(TOPBAR.normalize({ modes: { tasks: "auto", ghost: "hidden" } }).modes, { ...MODES, tasks: "auto" });
  assert.deepEqual(TOPBAR.normalize({ modes: { add: "auto", view: "bogus", sync: "always" } }).modes, { ...MODES, sync: "always" },
    "Add offers no auto; the View menu no such mode");
  assert.deepEqual(TOPBAR.normalize({ order: ["view", "ghost", "add"] }).order, ["view", "add", "tasks", "search", "share", "sync", "inbox"],
    "the items the order lacks follow the ones before them");
  for (const bad of [null, undefined, "always", 3, ["always"]]) assert.equal(TOPBAR.normalize(bad), undefined);
  assert.ok(TOPBAR.isDefault(TOPBAR.DEFAULT));
  assert.ok(TOPBAR.isDefault({ modes: { ghost: "hidden" } }));
  assert.ok(!TOPBAR.isDefault({ modes: { search: "hidden" } }));
  assert.ok(!TOPBAR.isDefault(TOPBAR.move(undefined, "view", 0)), "another order");
});

test("places: the preference on a desktop, the phone's bar without the items it has no room for, a guide everything", () => {
  const places = (value, options) => Object.fromEntries(TOPBAR.place(value, options).map((x) => [x.id, x.at]));
  const all = (place) => Object.fromEntries(TOPBAR_ITEMS.map((item) => [item.id, place]));
  assert.deepEqual(places(TOPBAR.DEFAULT, { active: { sync: true } }), all("bar"));
  assert.deepEqual(places(undefined, { active: { sync: true } }), all("bar"), "no preference reads as the default");
  assert.deepEqual(TOPBAR.place(undefined).map((x) => x.id), ORDER);
  assert.deepEqual(TOPBAR.place(TOPBAR.move(undefined, "search", 0)).map((x) => x.id).slice(0, 2), ["search", "add"], "in the layout's order");

  const quiet = { modes: { ...MODES, tasks: "auto", inbox: "auto", search: "hidden" } };
  const desk = places(quiet, { active: { sync: true } });
  assert.equal(desk.tasks, "menu");
  assert.equal(desk.inbox, "menu");
  assert.equal(desk.search, "menu");
  assert.equal(places(quiet, { active: { tasks: true, inbox: true } }).tasks, "bar");
  assert.equal(places(quiet, { open: "inbox" }).inbox, "bar", "its popover is open");
  assert.equal(places(quiet, { open: "downloads" }).tasks, "bar", "the tasks' popover is the downloads panel");
  assert.equal(places(quiet, { open: "search" }).search, "menu", "a hidden item's popover opens off the bar");
  assert.equal(places({ modes: { sync: "hidden" } }, { active: { sync: true } }).sync, "menu");

  const phone = places(TOPBAR.DEFAULT, { phone: true, active: { sync: true } });
  assert.deepEqual(phone, { ...all("bar"), tasks: "menu", inbox: "menu", view: "menu" });
  assert.equal(places({ modes: { add: "hidden" } }, { phone: true }).add, "menu", "the phone's bar follows the preference");

  const hidden = { modes: all("hidden") };
  assert.deepEqual(places(hidden, { guide: true }), all("bar"), "a tour or hint shows every item");
  assert.equal(places(hidden, { guide: true, phone: true }).tasks, "menu", "but never one the phone's bar has no room for");
});
