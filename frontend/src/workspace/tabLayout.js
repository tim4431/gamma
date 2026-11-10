// How the open tabs are arranged into groups shown side by side
// (docs/dev/tab_groups.md): a tree whose leaves are tab groups and whose
// inner nodes are splits, left to right ("row") or top to bottom ("col"),
// the shape VS Code's editor grid and SiYuan's layout tree share
// (docs/research/tab-groups.md). One group is the live one: its active tab
// is the page App has open, in the full workbench; every other group shows
// its active tab as a read view (PagePane.jsx). With one group the tree is
// that group alone, and the tab strip stays in the topbar as it always was.
//
// A layout is {root, active, seq}: `root` a group {type: "group", id,
// tabs: [tabId…], active: tabId | null} or a split {type: "split", id, dir:
// "row" | "col", children: [node…], sizes: [percent…]}; `active` the live
// group's id; `seq` where the next node id comes from. Tab ids are page
// ids; a tab is in exactly one group. The tabs themselves (title, pin)
// stay the synced list App keeps; this is their arrangement in one
// browser. Everything here is pure: an operation returns a new layout, or
// its argument itself when nothing changed, so a caller may compare
// identities.

const SIDES = { left: ["row", 0], right: ["row", 1], top: ["col", 0], bottom: ["col", 1] };

const dedupe = (ids) => {
  const out = [];
  for (const id of Array.isArray(ids) ? ids : []) if (typeof id === "string" && id && !out.includes(id)) out.push(id);
  return out;
};
const clone = (layout) => JSON.parse(JSON.stringify(layout));
// The argument itself when the result says the same, so React sees no change.
const settle = (prev, next) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next);

export function singleLayout(tabIds = [], active = null) {
  const tabs = dedupe(tabIds);
  return { root: { type: "group", id: "g1", tabs, active: tabs.includes(active) ? active : null }, active: "g1", seq: 2 };
}

function walk(node, fn, parent = null, index = -1) {
  fn(node, parent, index);
  if (node.type === "split") node.children.forEach((child, i) => walk(child, fn, node, i));
}
function find(root, id) {
  let hit = null;
  walk(root, (node, parent, index) => { if (!hit && node.id === id) hit = { node, parent, index }; });
  return hit;
}
const firstGroup = (node) => (node.type === "group" ? node : firstGroup(node.children[0]));

export function groupsOf(layout) {
  const out = [];
  walk(layout.root, (node) => { if (node.type === "group") out.push(node); });
  return out;
}
export const groupById = (layout, id) => groupsOf(layout).find((g) => g.id === id) || null;
export const groupOf = (layout, tabId) => groupsOf(layout).find((g) => g.tabs.includes(tabId)) || null;
export const activeGroup = (layout) => groupById(layout, layout.active) || groupsOf(layout)[0] || null;
// Every tab, group by group in reading order: the synced list's order.
export const tabOrder = (layout) => groupsOf(layout).flatMap((g) => g.tabs);
export const isSingle = (layout) => layout.root.type === "group";

// A node leaves its split: its room goes to the neighbour before it (else
// after it), a split left with one child gives way to that child, and a live
// group that went hands the live role to the group nearest its place.
function dropNode(layout, id) {
  const hit = find(layout.root, id);
  if (!hit || !hit.parent) return;
  const { parent, index } = hit;
  const wasLive = !!groupById({ root: hit.node }, layout.active);
  const keep = index > 0 ? index - 1 : index + 1;
  parent.sizes[keep] = (parent.sizes[keep] || 0) + (parent.sizes[index] || 0);
  const heir = parent.children[keep];
  parent.children.splice(index, 1);
  parent.sizes.splice(index, 1);
  if (wasLive) layout.active = firstGroup(heir).id;
  unwrap(layout);
}
function unwrap(layout) {
  const lift = (node) => {
    if (node.type !== "split") return node;
    node.children = node.children.map(lift);
    return node.children.length === 1 ? node.children[0] : node;
  };
  layout.root = lift(layout.root);
}
// The groups named that ended up empty go, while another group remains.
function tidy(layout, emptied = []) {
  for (const id of emptied) {
    const g = groupById(layout, id);
    if (g && !g.tabs.length && groupsOf(layout).length > 1) dropNode(layout, id);
  }
  unwrap(layout);
  if (!groupById(layout, layout.active)) layout.active = groupsOf(layout)[0].id;
  return layout;
}
// The tab that takes a group's active place when `at` leaves it: the one
// that stood after it, else the last.
const heirOf = (tabs, at) => tabs[Math.min(at, tabs.length - 1)] ?? null;

// The group becomes the live one, showing `tabId` when given (null: its
// library view), else what it was showing.
export function activate(layout, groupId, tabId) {
  const next = clone(layout);
  const g = groupById(next, groupId);
  if (!g) return layout;
  if (tabId !== undefined) {
    if (tabId && !g.tabs.includes(tabId)) return layout;
    g.active = tabId || null;
  }
  next.active = g.id;
  return settle(layout, next);
}

// The arrangement follows the tabs list (reconcile) and the page on screen:
// tabs the list no longer has leave their groups (a group emptied that way
// goes, as a close does), each group's tabs take the list's order among
// them, tabs no group holds yet join the live group at its end, and `shownTabId`, the page App has open, is made its group's active
// tab with that group live — a page opened again while another group shows
// it brings that group forward. "" or null: the live group shows the
// library, no tab active.
export function reconcile(layout, tabIds, shownTabId) {
  const ids = dedupe(tabIds);
  const have = new Set(ids);
  const next = clone(layout);
  const placed = new Set();
  const emptied = [];
  for (const g of groupsOf(next)) {
    const before = g.tabs;
    // in the list's order within the group, which a reorder wrote there
    g.tabs = before.filter((id) => have.has(id) && !placed.has(id) && (placed.add(id), true))
      .sort((a, b) => ids.indexOf(a) - ids.indexOf(b));
    if (g.active && !g.tabs.includes(g.active)) g.active = heirOf(g.tabs, before.indexOf(g.active));
    if (!g.tabs.length && before.length) emptied.push(g.id);
  }
  tidy(next, emptied);
  const live = activeGroup(next);
  for (const id of ids) if (!placed.has(id)) live.tabs.push(id);
  if (shownTabId) {
    const g = groupOf(next, shownTabId);
    if (g) { g.active = shownTabId; next.active = g.id; }
  } else {
    live.active = null;
  }
  return settle(layout, next);
}

// A group's tabs in a new order: the same tabs, as a strip's reorder shows them.
export function reorderGroup(layout, groupId, ids) {
  const g = groupById(layout, groupId);
  if (!g || !Array.isArray(ids) || ids.length !== g.tabs.length || ids.some((id) => !g.tabs.includes(id)) || new Set(ids).size !== ids.length) return layout;
  const next = clone(layout);
  groupById(next, groupId).tabs = [...ids];
  return settle(layout, next);
}

// A tab into a group at an index among its tabs (clamped; within one group
// the index names the place before the tab is taken out, as a strip's
// reorder does). Across groups the tab becomes the target's active tab, and
// when it was the live group's page the live role goes with it: the page on
// screen moves frame, nothing opens. A group emptied by the move goes.
export function moveTab(layout, tabId, toGroupId, index) {
  const next = clone(layout);
  const from = groupOf(next, tabId);
  const to = groupById(next, toGroupId);
  if (!from || !to) return layout;
  const at = from.tabs.indexOf(tabId);
  const wasLive = next.active === from.id && from.active === tabId;
  from.tabs.splice(at, 1);
  const i = Math.max(0, Math.min(index ?? to.tabs.length, to.tabs.length));
  to.tabs.splice(i, 0, tabId);
  if (from !== to) {
    to.active = tabId;
    if (from.active === tabId) from.active = heirOf(from.tabs, at);
    if (wasLive) next.active = to.id;
    tidy(next, [from.id]);
  }
  return settle(layout, next);
}

// A new group beside `groupId` on `side` (left, right, top, bottom): a
// sibling in a split of that direction, which an existing parent of the
// same direction takes as one more child, each of the pair getting half the
// room. `tabId` moves into the new group (from whichever group holds it;
// the only tab of the split group itself cannot be split off, nothing
// changes) and the live role follows it as a move's does. Without a tab the
// new group is empty and becomes live, so the library shows in it to pick
// its page.
export function splitGroup(layout, groupId, side, tabId = null) {
  if (!SIDES[side]) return layout;
  const [dir, after] = SIDES[side];
  const next = clone(layout);
  const g = groupById(next, groupId);
  if (!g) return layout;
  const from = tabId ? groupOf(next, tabId) : null;
  if (tabId && !from) return layout;
  if (from === g && g.tabs.length === 1) return layout;
  const ng = { type: "group", id: `g${next.seq++}`, tabs: [], active: null };
  const { parent, index } = find(next.root, g.id);
  if (parent && parent.dir === dir) {
    const size = (parent.sizes[index] || 100 / parent.children.length) / 2;
    parent.sizes[index] = size;
    parent.children.splice(index + after, 0, ng);
    parent.sizes.splice(index + after, 0, size);
  } else {
    const split = { type: "split", id: `s${next.seq++}`, dir, children: after ? [g, ng] : [ng, g], sizes: [50, 50] };
    if (parent) parent.children[index] = split; else next.root = split;
  }
  if (from) {
    const at = from.tabs.indexOf(tabId);
    const wasLive = next.active === from.id && from.active === tabId;
    from.tabs.splice(at, 1);
    if (from.active === tabId) from.active = heirOf(from.tabs, at);
    ng.tabs.push(tabId);
    ng.active = tabId;
    if (wasLive) next.active = ng.id;
    tidy(next, [from.id]);
  } else {
    next.active = ng.id;
  }
  return next;
}

// A tab closed: its group shows `prefer` when that is one of its remaining
// tabs (the caller's latest-viewed), else the neighbour after the closed
// one. A group emptied goes while another remains; the live role passes to
// the group nearest its place.
export function removeTab(layout, tabId, { prefer = null } = {}) {
  const next = clone(layout);
  const g = groupOf(next, tabId);
  if (!g) return layout;
  const at = g.tabs.indexOf(tabId);
  g.tabs.splice(at, 1);
  if (g.active === tabId) g.active = prefer && g.tabs.includes(prefer) ? prefer : heirOf(g.tabs, at);
  tidy(next, [g.id]);
  return settle(layout, next);
}

// A group taken out of the arrangement: its tabs join the group that takes
// its room, at the end. The only group stays.
export function removeGroup(layout, groupId) {
  const next = clone(layout);
  const g = groupById(next, groupId);
  if (!g || groupsOf(next).length < 2) return layout;
  const { parent, index } = find(next.root, g.id);
  const heir = firstGroup(parent.children[index > 0 ? index - 1 : index + 1]);
  heir.tabs.push(...g.tabs.filter((id) => !heir.tabs.includes(id)));
  if (!heir.active && heir.tabs.length) heir.active = heir.tabs[0];
  g.tabs = [];
  dropNode(next, g.id);
  if (next.active === g.id) next.active = heir.id;
  return settle(layout, tidy(next));
}

// The sizes a split's children were dragged to, in percent of the split.
export function resizeSplit(layout, splitId, sizes) {
  const hit = find(layout.root, splitId);
  if (!hit || hit.node.type !== "split" || !Array.isArray(sizes) || sizes.length !== hit.node.children.length) return layout;
  const rounded = sizes.map((s) => Math.round(Number(s) * 100) / 100);
  if (rounded.some((s) => !Number.isFinite(s))) return layout;
  if (rounded.every((s, i) => Math.abs(s - hit.node.sizes[i]) < 0.5)) return layout;
  const next = clone(layout);
  find(next.root, splitId).node.sizes = rounded;
  return next;
}

// Every tab in one group, in reading order, the live group's page still open.
export function joinAll(layout) {
  if (isSingle(layout)) return layout;
  const id = `g${layout.seq}`;
  return { root: { type: "group", id, tabs: tabOrder(layout), active: activeGroup(layout)?.active ?? null }, active: id, seq: layout.seq + 1 };
}

// A stored layout against the tabs that are open: an unknown shape, or
// nothing open across several groups, gives one group of every tab; else
// the tree is kept with its ids renumbered, tabs that are not open dropped,
// a tab met twice kept where it was first, a split left with one child
// unwrapped, a group whose every tab is gone dropped (one saved empty stays),
// sizes made to fit, the tabs no group holds added to the live group, and a
// live group that is gone replaced by the first.
export function normalizeLayout(value, tabIds) {
  const ids = dedupe(tabIds);
  const have = new Set(ids);
  const fallback = () => singleLayout(ids);
  try {
    if (!value || typeof value !== "object" || !value.root || typeof value.root !== "object") return fallback();
    let seq = 1;
    const seen = new Set();
    const map = new Map();
    const norm = (node) => {
      if (!node || typeof node !== "object") return null;
      if (node.type === "group") {
        const named = Array.isArray(node.tabs) ? node.tabs.filter((id) => typeof id === "string") : [];
        const tabs = named.filter((id) => have.has(id) && !seen.has(id) && (seen.add(id), true));
        // A group whose every tab closed since (on another device, say) goes
        // as a close would have taken it; one saved empty was meant so.
        if (named.length && !tabs.length) return null;
        const g = { type: "group", id: `g${seq++}`, tabs, active: tabs.includes(node.active) ? node.active : (tabs[0] ?? null) };
        if (typeof node.id === "string") map.set(node.id, g.id);
        return g;
      }
      if (node.type === "split" && Array.isArray(node.children)) {
        const kept = node.children.map((child) => [child, norm(child)]).filter(([, out]) => out);
        if (!kept.length) return null;
        if (kept.length === 1) return kept[0][1];
        const dir = node.dir === "col" ? "col" : "row";
        let sizes = kept.map(([child]) => (Array.isArray(node.sizes) ? Number(node.sizes[node.children.indexOf(child)]) : NaN));
        const total = sizes.reduce((sum, s) => sum + s, 0);
        if (sizes.some((s) => !Number.isFinite(s) || s <= 0) || !(total > 0)) sizes = kept.map(() => 100 / kept.length);
        else sizes = sizes.map((s) => Math.round((s / total) * 10000) / 100);
        return { type: "split", id: `s${seq++}`, dir, children: kept.map(([, out]) => out), sizes };
      }
      return null;
    };
    const root = norm(value.root) || { type: "group", id: `g${seq++}`, tabs: [], active: null };
    const layout = { root, active: "", seq };
    layout.active = map.get(value.active) && groupById(layout, map.get(value.active)) ? map.get(value.active) : groupsOf(layout)[0].id;
    if (!ids.length && groupsOf(layout).length > 1) return fallback();
    const live = activeGroup(layout);
    for (const id of ids) if (!seen.has(id)) live.tabs.push(id);
    return layout;
  } catch {
    return fallback();
  }
}
