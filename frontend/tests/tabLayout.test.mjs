// The arrangement of the open tabs into groups side by side
// (workspace/tabLayout.js, docs/dev/tab_groups.md): splits, moves, closes,
// what a stored layout is made into, and the tabs list the arrangement
// follows.
import assert from "node:assert/strict";
import test from "node:test";
import {
  activate, activeGroup, groupOf, groupsOf, isSingle, joinAll, moveTab, normalizeLayout, reconcile, removeGroup, removeTab,
  reorderGroup, resizeSplit, singleLayout, splitGroup, tabOrder,
} from "../src/workspace/tabLayout.js";

const ids = (layout) => groupsOf(layout).map((g) => g.tabs.join(","));
const shape = (node) => (node.type === "group" ? node.tabs.join(",") : `${node.dir}(${node.children.map(shape).join(" | ")})`);

test("one group is the whole layout, and splitting it off to a side makes a split of two", () => {
  const one = singleLayout(["a", "b", "c"], "a");
  assert.ok(isSingle(one));
  assert.equal(activeGroup(one).active, "a");
  assert.deepEqual(tabOrder(one), ["a", "b", "c"]);

  // b to the right: a row split, b alone and active on the right; a stays on screen.
  const right = splitGroup(one, "g1", "right", "b");
  assert.equal(shape(right.root), "row(a,c | b)");
  assert.deepEqual(right.root.sizes, [50, 50]);
  assert.equal(right.active, "g1", "the live group stays where the open page is");
  assert.equal(groupsOf(right)[1].active, "b");
  // the live page itself split off: the live role goes with it
  const live = splitGroup(one, "g1", "bottom", "a");
  assert.equal(shape(live.root), "col(b,c | a)");
  assert.equal(activeGroup(live).active, "a");
  assert.equal(groupsOf(live)[0].active, "b", "the group left behind shows the tab after the one that left");
  // to the left or the top: the new group comes first
  assert.equal(shape(splitGroup(one, "g1", "left", "c").root), "row(c | a,b)");
  assert.equal(shape(splitGroup(one, "g1", "top", "c").root), "col(c | a,b)");
  // the only tab of a group cannot be split off it
  const lone = singleLayout(["a"], "a");
  assert.equal(splitGroup(lone, "g1", "right", "a"), lone);
  // an unknown side or group: nothing
  assert.equal(splitGroup(one, "g1", "sideways", "b"), one);
  assert.equal(splitGroup(one, "nope", "right", "b"), one);
});

test("a split of the same direction takes one more child; a different one nests", () => {
  const two = splitGroup(singleLayout(["a", "b", "c"], "a"), "g1", "right", "b");
  const [left, rightG] = groupsOf(two);
  // c to the right of the right group: three columns, the right one's room halved
  const three = splitGroup(two, rightG.id, "right", "c");
  assert.equal(shape(three.root), "row(a | b | c)");
  assert.deepEqual(three.root.sizes, [50, 25, 25]);
  // a stack under the left group nests a column split inside the row
  const nested = splitGroup(two, left.id, "bottom", "c");
  assert.equal(shape(nested.root), "row(col(a | c) | b)");
  assert.deepEqual(tabOrder(nested), ["a", "c", "b"]);
});

test("an empty group split off becomes live, for the library to pick its page", () => {
  const one = singleLayout(["a"], "a");
  const next = splitGroup(one, "g1", "right");
  assert.equal(shape(next.root), "row(a | )");
  const empty = groupsOf(next)[1];
  assert.equal(next.active, empty.id);
  assert.equal(empty.active, null);
  // closing it hands the live role back
  const back = removeGroup(next, empty.id);
  assert.ok(isSingle(back));
  assert.equal(activeGroup(back).active, "a");
  // the only group cannot be removed
  assert.equal(removeGroup(one, "g1"), one);
});

test("a tab moved across groups becomes the target's active tab and leaves an emptied group behind", () => {
  const two = splitGroup(singleLayout(["a", "b", "c"], "a"), "g1", "right", "c");
  const [left, right] = groupsOf(two);
  // b joins c on the right, before it
  const moved = moveTab(two, "b", right.id, 0);
  assert.equal(shape(moved.root), "row(a | b,c)");
  assert.equal(groupsOf(moved)[1].active, "b");
  assert.equal(moved.active, left.id, "the live group is unchanged while its page stays");
  // the live page moved: the live role follows it, and the group it left shows its next tab
  const live = moveTab(two, "a", right.id, 99);
  assert.equal(shape(live.root), "row(b | c,a)");
  assert.equal(live.active, groupsOf(live)[1].id);
  assert.equal(groupsOf(live)[0].active, "b");
  // the last tab out of a group closes the group: back to one
  const lastOut = moveTab(moved, "a", groupsOf(moved)[1].id, 0);
  assert.ok(isSingle(lastOut));
  assert.deepEqual(tabOrder(lastOut), ["a", "b", "c"]);
  assert.equal(activeGroup(lastOut).active, "a");
  // within a group a move is a reorder at the index named before the tab is taken out, and the active tab stays
  const re = moveTab(two, "a", left.id, 2);
  assert.deepEqual(groupsOf(re)[0].tabs, ["b", "a"]);
  assert.equal(groupsOf(re)[0].active, "a");
  assert.equal(moveTab(two, "zz", left.id, 0), two, "an unknown tab moves nothing");
});

test("closing a tab shows the latest-viewed or the next tab; a group closed by its last tab gives its room away", () => {
  const two = splitGroup(singleLayout(["a", "b", "c"], "a"), "g1", "right", "c");
  const closedB = removeTab(two, "b");
  assert.equal(shape(closedB.root), "row(a | c)");
  // the active tab closed: the neighbour after it, else the one before
  const one = singleLayout(["a", "b", "c"], "b");
  assert.equal(activeGroup(removeTab(one, "b")).active, "c");
  assert.equal(activeGroup(removeTab(singleLayout(["a", "b"], "b"), "b")).active, "a");
  assert.equal(activeGroup(removeTab(one, "b", { prefer: "a" })).active, "a", "the caller's latest-viewed wins");
  assert.equal(activeGroup(removeTab(one, "b", { prefer: "zz" })).active, "c", "a preference that is not there is ignored");
  // the right group's only tab closed: the group goes and the left one has the whole width
  const three = splitGroup(two, groupsOf(two)[1].id, "right", "b");
  assert.deepEqual(three.root.sizes, [50, 25, 25]);
  const gone = removeTab(three, "c");
  assert.equal(shape(gone.root), "row(a | b)");
  assert.deepEqual(gone.root.sizes, [75, 25]);
  // the live group's last tab closed: the live role passes to the neighbour
  const liveGone = removeTab(activate(two, groupsOf(two)[1].id), "c");
  assert.ok(isSingle(liveGone));
  assert.equal(liveGone.active, groupsOf(liveGone)[0].id);
  assert.equal(activeGroup(liveGone).active, "a");
  // the last tab of the only group leaves it empty, still there
  const empty = removeTab(singleLayout(["a"], "a"), "a");
  assert.equal(shape(empty.root), "");
  assert.equal(activeGroup(empty).active, null);
  assert.equal(removeTab(two, "zz"), two);
});

test("activate names the live group and what it shows; join puts every tab back in one group", () => {
  const two = splitGroup(singleLayout(["a", "b", "c"], "a"), "g1", "right", "c");
  const right = groupsOf(two)[1].id;
  const on = activate(two, right);
  assert.equal(on.active, right);
  assert.equal(activeGroup(on).active, "c", "what the group was showing");
  assert.equal(activate(two, right, "a"), two, "a tab the group does not hold: nothing");
  assert.equal(activeGroup(activate(two, "g1", null)).active, null, "null: the group shows the library");
  assert.equal(activate(on, right), on, "no change is the same object");
  const joined = joinAll(on);
  assert.ok(isSingle(joined));
  assert.deepEqual(tabOrder(joined), ["a", "b", "c"]);
  assert.equal(activeGroup(joined).active, "c");
  assert.equal(joinAll(joined), joined);
});

test("the arrangement follows the tabs list and the page on screen", () => {
  const two = splitGroup(singleLayout(["a", "b", "c"], "a"), "g1", "right", "c");
  // a new tab lands in the live group at its end
  const opened = reconcile(two, ["a", "b", "c", "d"], "d");
  assert.equal(shape(opened.root), "row(a,b,d | c)");
  assert.equal(activeGroup(opened).active, "d");
  // a tab closed elsewhere leaves its group; its group shows the next
  const closed = reconcile(opened, ["b", "c", "d"], "d");
  assert.equal(shape(closed.root), "row(b,d | c)");
  // the right group's tab closed elsewhere: the group goes
  const rightGone = reconcile(opened, ["a", "b", "d"], "d");
  assert.ok(isSingle(rightGone));
  // the page on screen is a tab another group holds: that group comes forward
  const forward = reconcile(two, ["a", "b", "c"], "c");
  assert.equal(forward.active, groupsOf(two)[1].id);
  assert.equal(activeGroup(forward).active, "c");
  // the library on screen: the live group shows no tab
  assert.equal(activeGroup(reconcile(two, ["a", "b", "c"], "")).active, null);
  // nothing to do is the same object
  assert.equal(reconcile(two, ["a", "b", "c"], "a"), two);
  // a tab listed twice is placed once
  assert.deepEqual(tabOrder(reconcile(two, ["a", "b", "c", "c", "e"], "a")), ["a", "b", "e", "c"]);
  // the list's order holds within a group (a pin moved a tab first), not across groups
  assert.equal(shape(reconcile(two, ["c", "b", "a"], "a").root), "row(b,a | c)");
});

test("a group's tabs take the order a strip's reorder gives them", () => {
  const two = splitGroup(singleLayout(["a", "b", "c"], "a"), "g1", "right", "c");
  const re = reorderGroup(two, "g1", ["b", "a"]);
  assert.equal(shape(re.root), "row(b,a | c)");
  assert.equal(activeGroup(re).active, "a");
  assert.equal(reorderGroup(two, "g1", ["a", "b"]), two, "the same order is the same object");
  assert.equal(reorderGroup(two, "g1", ["a", "c"]), two, "other tabs: nothing");
  assert.equal(reorderGroup(two, "g1", ["a", "a"]), two);
  assert.equal(reorderGroup(two, "nope", ["a", "b"]), two);
});

test("a stored layout is checked against the open tabs; anything else becomes one group", () => {
  const two = splitGroup(singleLayout(["a", "b", "c"], "a"), "g1", "right", "c");
  const saved = JSON.parse(JSON.stringify(two));
  // the same tabs: the same shape, ids renumbered, the live group kept
  const back = normalizeLayout(saved, ["a", "b", "c"]);
  assert.equal(shape(back.root), shape(two.root));
  assert.equal(activeGroup(back).active, "a");
  assert.deepEqual(back.root.sizes, [50, 50]);
  // a tab gone since, and a new one: dropped, and added to the live group
  const changed = normalizeLayout(saved, ["a", "c", "e"]);
  assert.equal(shape(changed.root), "row(a,e | c)");
  // the right group's tab gone: its group goes with it, the sizes made whole
  const rightGone = normalizeLayout(saved, ["a", "b"]);
  assert.ok(isSingle(rightGone));
  // a tab met twice keeps its first place; a live group that is gone gives way to the first
  const dup = { ...saved, root: { ...saved.root, children: [saved.root.children[0], { type: "group", id: "g9", tabs: ["a", "c"], active: "c" }] }, active: "zz" };
  const fixed = normalizeLayout(dup, ["a", "b", "c"]);
  assert.equal(shape(fixed.root), "row(a,b | c)");
  assert.equal(fixed.active, groupsOf(fixed)[0].id);
  // bad sizes are evened out; a split of one child unwraps
  const odd = { root: { type: "split", dir: "col", children: [{ type: "split", dir: "row", children: [{ type: "group", tabs: ["a"] }], sizes: [1] }, { type: "group", tabs: ["b"] }], sizes: ["x", 1] }, active: "g1" };
  const evened = normalizeLayout(odd, ["a", "b"]);
  assert.equal(shape(evened.root), "col(a | b)");
  assert.deepEqual(evened.root.sizes, [50, 50]);
  // garbage, or nothing open across several groups: one group of the open tabs
  for (const junk of [null, 7, "x", {}, { root: 3 }, { root: { type: "nope" } }]) {
    assert.deepEqual(normalizeLayout(junk, ["a", "b"]), singleLayout(["a", "b"]));
  }
  assert.ok(isSingle(normalizeLayout(saved, [])));
  assert.equal(groupOf(back, "c").id, groupsOf(back)[1].id);
});

test("a split keeps the sizes its sash was dragged to, within half a percent", () => {
  const two = splitGroup(singleLayout(["a", "b"], "a"), "g1", "right", "b");
  const sized = resizeSplit(two, two.root.id, [30.004, 69.996]);
  assert.deepEqual(sized.root.sizes, [30, 70]);
  assert.equal(resizeSplit(sized, sized.root.id, [30.2, 69.8]), sized, "a nudge within tolerance is the same object");
  assert.equal(resizeSplit(two, two.root.id, [1, 2, 3]), two, "the wrong count is ignored");
  assert.equal(resizeSplit(two, "nope", [50, 50]), two);
  assert.deepEqual(ids(two), ["a", "b"]);
});
