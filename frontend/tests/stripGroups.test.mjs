// Chrome's tab groups in a strip (workspace/stripGroups.js,
// docs/dev/strip_groups.md): the rules a synced tabs list is held to, what
// a strip's drag does to a tab's group, where a tab lands in a group as it
// is grouped, removed, collapsed past or moved to the other pane, and a
// group's tabs gathered into one pane of this browser's arrangement.
import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_GROUP_TITLE, collapseHeir, gatherPanes, groupTab, joinGroup, landTab, leastUsedColor, leaveGroup, moveInStrip,
  normalizeTabs, setGroup, stripRuns, takeGroups, ungroup,
} from "../src/workspace/stripGroups.js";
import { activeGroup, groupsOf, singleLayout, splitGroup } from "../src/workspace/tabLayout.js";

const g = (id, more = {}) => ({ id, title: id.toUpperCase(), color: "blue", ...more });
// "a b:x c:x d" → tabs a, b, c, d with b and c in group x
const list = (spec) => spec.split(" ").map((word) => {
  const [id, group] = word.split(":");
  const pinned = id.startsWith("^");
  const tab = { id: pinned ? id.slice(1) : id, title: id };
  if (pinned) tab.pinned = true;
  if (group) tab.group = g(group);
  return tab;
});
// the same notation back, from a list
const spec = (tabs) => (tabs ? tabs.map((t) => `${t.pinned ? "^" : ""}${t.id}${t.group ? `:${t.group.id}` : ""}`).join(" ") : null);

test("a kept list has pinned tabs first and ungrouped, and each group as one run with one look", () => {
  assert.equal(spec(normalizeTabs(list("a:x ^p:x b"))), "^p a:x b");
  // a group met twice is the longest run's; the shorter run's tabs leave it
  assert.equal(spec(normalizeTabs(list("a:x b c:x d:x"))), "a b c:x d:x");
  assert.equal(spec(normalizeTabs(list("a:x b c:x"))), "a:x b c", "a tie keeps the first run");
  // what is not a group goes; an unknown colour is grey, a long title cut
  const odd = normalizeTabs([
    { id: "a", title: "a", group: "x" },
    { id: "b", title: "b", group: { id: "y", title: "z".repeat(80), color: "teal" } },
  ]);
  assert.equal(odd[0].group, undefined);
  assert.deepEqual(odd[1].group, { id: "y", title: "z".repeat(MAX_GROUP_TITLE), color: "grey" });
  // every member takes the look of the run's first
  const mixed = normalizeTabs([
    { id: "a", title: "a", group: g("x", { collapsed: true }) },
    { id: "b", title: "b", group: g("x", { title: "Other", color: "red" }) },
  ]);
  assert.deepEqual(mixed.map((t) => t.group), [g("x", { collapsed: true }), g("x", { collapsed: true })]);
  // a kept list is kept as it is, its tabs the same objects
  const kept = normalizeTabs(list("^p a:x b:x c"));
  assert.ok(normalizeTabs(kept).every((tab, i) => tab === kept[i]));
});

test("a group's tabs stand in one pane of this browser's arrangement, what is on screen staying", () => {
  // another device grouped a and c; here c stands in the other pane, showing
  const two = splitGroup(singleLayout(["a", "b", "c", "d"], "a"), "g1", "right", "c");
  const gathered = gatherPanes(two, list("a:x c:x b d"));
  assert.deepEqual(groupsOf(gathered).map((g) => g.tabs.join(",")), ["a,b,d,c"], "the pane emptied goes");
  assert.equal(activeGroup(gathered).active, "a", "the page on screen stays");
  // a group already in one pane, or tabs no pane holds yet: nothing moves
  assert.equal(gatherPanes(two, list("a:x b:x c d")), two);
  assert.equal(gatherPanes(two, list("a:x n:x b c d")), two);
});

test("a strip draws a chip before each group's run", () => {
  const runs = stripRuns(list("a b:x c:x d:y e"));
  assert.deepEqual(runs.map((r) => (r.group ? `${r.group.id}(${r.tabs.map((t) => t.id)})` : r.tab.id)), ["a", "x(b,c)", "y(d)", "e"]);
});

test("grouping a tab: a new group where it stands, the strip's least used colour, out of the group it was in", () => {
  const tabs = list("a b:x c:x d");
  assert.equal(leastUsedColor(tabs), "red", "blue is taken");
  // c from the middle of x: it stands after x's run first, so x stays one run
  const made = groupTab(list("a b:x c:x e:x d"), "c", "n");
  assert.equal(spec(made), "a b:x e:x c:n d");
  assert.equal(made[3].group.title, "");
  assert.equal(spec(groupTab(list("^p a"), "p", "n")), "^p a", "a pinned tab is never grouped");
  // into a group: to the end of its run, a collapsed group opening
  assert.equal(spec(joinGroup(list("a b:x c:x d"), "a", "x")), "b:x c:x a:x d");
  assert.equal(spec(joinGroup(list("a:y b:x c:x d"), "a", "x")), "b:x c:x a:x d");
  const shut = setGroup(list("a b:x c:x"), "x", { collapsed: true });
  assert.deepEqual(joinGroup(shut, "a", "x").map((t) => !!t.group.collapsed), [false, false, false]);
  // out of one: right after its run, wherever it stood in it
  assert.equal(spec(leaveGroup(list("a b:x c:x d:x e"), "b")), "a c:x d:x b e");
  assert.equal(spec(leaveGroup(list("a b:x c"), "b")), "a b c", "the only tab stays where it is");
  assert.equal(spec(ungroup(list("a:x b:x c:y"), "x")), "a b c:y");
  assert.deepEqual(setGroup(list("a:x b:x"), "x", { collapsed: true, title: "Read" }).map((t) => t.group),
    [g("x", { title: "Read", collapsed: true }), g("x", { title: "Read", collapsed: true })]);
});

test("a tab dragged in its strip joins the group it passes over and leaves its own past a tab outside it", () => {
  const strip = list("a b:x c:x d");
  // a over x's first tab, and over its last: in x, at that place
  assert.equal(spec(moveInStrip(strip, "a", "b")), "b:x a:x c:x d");
  assert.equal(spec(moveInStrip(strip, "a", "c")), "b:x c:x a:x d");
  assert.equal(spec(moveInStrip(strip, "d", "c")), "a b:x d:x c:x");
  // x's tabs past a tab outside it: out of x
  assert.equal(spec(moveInStrip(strip, "c", "d")), "a b:x d c");
  assert.equal(spec(moveInStrip(strip, "b", "a")), "b a c:x d");
  // within the group: a reorder, still in it
  assert.equal(spec(moveInStrip(strip, "b", "c")), "a c:x b:x d");
  // a group's only tab carries its group past a tab outside it, and joins another it passes over
  assert.equal(spec(moveInStrip(list("a:x b c:y"), "a", "b")), "b a:x c:y");
  assert.equal(spec(moveInStrip(list("a:x b c:y"), "a", "c")), "b c:y a:y");
  // a collapsed group, which shows one tab at most, is passed whole and never joined
  const shut = setGroup(list("a b:x c:x d"), "x", { collapsed: true });
  assert.equal(spec(moveInStrip(shut, "a", "b")), "b:x c:x a d");
  assert.equal(spec(moveInStrip(shut, "d", "b")), "a d b:x c:x");
  // over the chip: just before the group, out of it; over the strip's end: at the end
  assert.equal(spec(moveInStrip(strip, "c", { group: "x" })), "a c b:x d");
  assert.equal(spec(moveInStrip(strip, "a", { group: "x" })), null, "already there");
  assert.equal(spec(moveInStrip(list("a b:x c:x"), "c", null)), "a b:x c");
  // pinned and unpinned never trade places
  assert.equal(moveInStrip(list("^p a b"), "a", "p"), null);
  assert.equal(moveInStrip(list("^p a"), "p", null), null);
  assert.equal(moveInStrip(strip, "a", "a"), null);
});

test("a chip dragged carries its whole group past a tab, or past another group whole", () => {
  const strip = list("a b:x c:x d e:y f:y g");
  assert.equal(spec(moveInStrip(strip, { group: "x" }, "d")), "a d b:x c:x e:y f:y g");
  assert.equal(spec(moveInStrip(strip, { group: "x" }, "a")), "b:x c:x a d e:y f:y g");
  // over any tab of y, or y's chip: past y, never into it
  assert.equal(spec(moveInStrip(strip, { group: "x" }, "e")), "a d e:y f:y b:x c:x g");
  assert.equal(spec(moveInStrip(strip, { group: "y" }, { group: "x" })), "a e:y f:y b:x c:x d g");
  assert.equal(spec(moveInStrip(strip, { group: "x" }, null)), "a d e:y f:y g b:x c:x");
  assert.equal(moveInStrip(strip, { group: "x" }, "b"), null, "over its own tab");
  assert.equal(moveInStrip(list("^p a:x"), { group: "x" }, "p"), null, "never among the pinned tabs");
});

test("a strip's drag goes onto the list with the groups it gave, the other pane's tabs as they were", () => {
  // the strip is a, b, c; e and f stand in the other pane, f in a group of its own
  const tabs = list("a b:x c:x e f:y");
  const strip = moveInStrip(tabs.slice(0, 3), "c", "a");
  assert.equal(spec(takeGroups(tabs, strip)), "a b:x c e f:y");
});

test("collapsing the group the strip's page is in opens the nearest tab outside it", () => {
  const strip = list("a b:x c:x d");
  assert.equal(collapseHeir(strip, "x", "a"), undefined, "the page is not in the group: nothing to open");
  assert.equal(collapseHeir(strip, "x", "b"), "d", "after the group first");
  assert.equal(collapseHeir(list("a b:x c:x"), "x", "c"), "a", "then before it");
  assert.equal(collapseHeir(list("b:x c:x"), "x", "b"), null, "nothing outside it: the library");
  // a tab in sight before one hidden in another collapsed group
  const hidden = list("a b:x c:y");
  hidden[2].group.collapsed = true;
  assert.equal(collapseHeir(hidden, "x", "b"), "a");
});

test("a tab moved into the other pane keeps its group only beside it or alone in it, else joins the group it lands inside", () => {
  const tabs = list("a:x b:x c d:y e:y");
  // a moved next to c, its group left behind: out of it
  assert.equal(spec(landTab(tabs, "a", ["c", "a"])), "a b:x c d:y e:y");
  // dropped between d and e: into y
  assert.equal(spec(landTab(tabs, "a", ["c", "d", "a", "e"])), "a:y b:x c d:y e:y");
  // beside a tab of its own group, or its group's only tab: kept
  assert.equal(spec(landTab(tabs, "a", ["b", "a"])), "a:x b:x c d:y e:y");
  assert.equal(spec(landTab(list("a:x c"), "a", ["a"])), "a:x c");
  // on an edge of a group: not inside it
  assert.equal(spec(landTab(list("a d:y e:y"), "a", ["d", "e", "a"])), "a d:y e:y");
  // a group's only tab dropped inside another group joins it, so that group stays one run
  assert.equal(spec(landTab(list("a:x d:y e:y"), "a", ["d", "a", "e"])), "a:y d:y e:y");
});
