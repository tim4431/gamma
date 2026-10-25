// The topbar's items (app/topbarItems.js): the preference's shape, the
// Settings rows' words, and where each item goes.
import test from "node:test";
import assert from "node:assert/strict";
import { PREFS } from "../src/app/prefDefs.js";
import {
  DEFAULT_TOPBAR, TOPBAR_ITEMS, TOPBAR_MODES,
  barShows, isDefaultTopbar, modeLabel, modeTip, normalizeTopbar, topbarPlaces,
} from "../src/app/topbarItems.js";

test("every item offers known modes, auto only with its own word, and has the parts its row and anchor need", () => {
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
      assert.ok(TOPBAR_MODES.includes(mode), `${item.id}: unknown mode ${mode}`);
      assert.ok(modeLabel(item, mode) !== mode, `${item.id}: the ${mode} choice has a word`);
    }
    if (item.modes.includes("auto")) assert.ok(item.labels?.auto, `${item.id} names its auto`);
    assert.ok(modeTip(item, "hidden"), `${item.id}: the hidden choice says where the item goes`);
  }
});

test("the default is each item's first mode, and the preference stores it", () => {
  assert.deepEqual(DEFAULT_TOPBAR, { add: "always", tasks: "always", search: "always", share: "always", sync: "auto", inbox: "always", view: "always" });
  for (const item of TOPBAR_ITEMS) assert.equal(DEFAULT_TOPBAR[item.id], item.modes[0]);
  assert.equal(PREFS.topbar.scope, "account");
  assert.equal(PREFS.topbar.default, DEFAULT_TOPBAR);
  assert.deepEqual(PREFS.topbar.parse(PREFS.topbar.serialize({ inbox: "auto" })), { ...DEFAULT_TOPBAR, inbox: "auto" });
  assert.equal(PREFS.topbar.parse("not json"), undefined);
  assert.equal(PREFS.syncPillScope, undefined, "the old sync pill preference is gone");
});

test("a stored value comes back whole: unknown ids dropped, missing or unoffered modes take the default", () => {
  assert.deepEqual(normalizeTopbar({}), DEFAULT_TOPBAR);
  assert.deepEqual(normalizeTopbar({ tasks: "auto", ghost: "hidden" }), { ...DEFAULT_TOPBAR, tasks: "auto" });
  assert.deepEqual(normalizeTopbar({ add: "auto", view: "bogus", sync: "always" }), { ...DEFAULT_TOPBAR, sync: "always" },
    "Add offers no auto; the View menu no such mode");
  for (const bad of [null, undefined, "always", 3, ["always"]]) assert.equal(normalizeTopbar(bad), undefined);
  assert.ok(isDefaultTopbar(DEFAULT_TOPBAR));
  assert.ok(isDefaultTopbar({ ghost: "hidden" }));
  assert.ok(!isDefaultTopbar({ search: "hidden" }));
});

test("the bar shows always, auto while active or open, hidden never", () => {
  assert.equal(barShows("always"), true);
  assert.equal(barShows("auto"), false);
  assert.equal(barShows("auto", { active: true }), true);
  assert.equal(barShows("auto", { open: true }), true, "an open popover keeps its button");
  assert.equal(barShows("hidden", { active: true, open: true }), false);
});

test("places: the preference on a desktop, the phone's bar without the items it has no room for, a guide everything", () => {
  const all = (place) => Object.fromEntries(TOPBAR_ITEMS.map((item) => [item.id, place]));
  assert.deepEqual(topbarPlaces(DEFAULT_TOPBAR, { active: { sync: true } }), all("bar"));
  assert.deepEqual(topbarPlaces(undefined, { active: { sync: true } }), all("bar"), "no preference reads as the default");

  const quiet = { ...DEFAULT_TOPBAR, tasks: "auto", inbox: "auto", search: "hidden" };
  const desk = topbarPlaces(quiet, { active: { sync: true } });
  assert.equal(desk.tasks, "menu");
  assert.equal(desk.inbox, "menu");
  assert.equal(desk.search, "menu");
  assert.equal(topbarPlaces(quiet, { active: { tasks: true, inbox: true } }).tasks, "bar");
  assert.equal(topbarPlaces(quiet, { open: "inbox" }).inbox, "bar", "its popover is open");
  assert.equal(topbarPlaces(quiet, { open: "search" }).search, "menu", "a hidden item's popover opens off the bar");
  assert.equal(topbarPlaces({ ...DEFAULT_TOPBAR, sync: "hidden" }, { active: { sync: true } }).sync, "menu");

  const phone = topbarPlaces(DEFAULT_TOPBAR, { phone: true, active: { sync: true } });
  assert.deepEqual(phone, { ...all("bar"), tasks: "menu", inbox: "menu", view: "menu" });
  assert.equal(topbarPlaces({ ...DEFAULT_TOPBAR, add: "hidden" }, { phone: true }).add, "menu", "the phone's bar follows the preference");

  const hidden = all("hidden");
  assert.deepEqual(topbarPlaces(hidden, { guide: true }), all("bar"), "a tour or hint shows every item");
  assert.equal(topbarPlaces(hidden, { guide: true, phone: true }).tasks, "menu", "but never one the phone's bar has no room for");
});
