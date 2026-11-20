// Chrome's tab groups, in a tab strip (docs/dev/strip_groups.md): a named,
// coloured run of tabs behind a chip, which a click collapses. Not the panes
// side by side, which tabLayout.js calls groups; here they are strip groups.
//
// A group lives on its tabs, in the synced `open-tabs` list: every member
// carries `group: {id, title, color, collapsed?}`, so a tab, its group and
// the group's look go to the server in one write. The rules every list is
// held to (normalizeTabs): pinned tabs first and never grouped, a group's
// members one run, every member the same look. On this browser a group's
// tabs also stand in one pane (gatherPanes). Everything here is pure; an
// operation returns a new list (or layout).
import { T } from "../shared/i18n/locales.js";
import { groupOf, moveTabs } from "./tabLayout.js";

// Chrome's nine, in the order a new group takes them; the CSS reads each
// from its token, --strip-group-<name>.
export const STRIP_GROUP_COLORS = [
  ["blue", T("Blue")], ["red", T("Red")], ["yellow", T("Yellow")], ["green", T("Green")], ["pink", T("Pink")],
  ["purple", T("Purple")], ["cyan", T("Cyan")], ["orange", T("Orange")], ["grey", T("Gray")],
];
const COLORS = STRIP_GROUP_COLORS.map(([name]) => name);
export const MAX_GROUP_TITLE = 40;
// A colour's look on a chip, a tab, a dot or a swatch: the component-local
// --strip-group the CSS paints with.
export const groupTint = (color) => ({ "--strip-group": `var(--strip-group-${color})` });

// A stored group as it is kept: an id, a title, a known colour, collapsed
// only when true. Null for what is not one.
function cleanGroup(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.id !== "string" || !raw.id) return null;
  const title = typeof raw.title === "string" ? raw.title.slice(0, MAX_GROUP_TITLE) : "";
  const color = COLORS.includes(raw.color) ? raw.color : "grey";
  return raw.collapsed === true ? { id: raw.id, title, color, collapsed: true } : { id: raw.id, title, color };
}
const sameGroup = (a, b) => (!a && !b) || (!!a && !!b && a.id === b.id && a.title === b.title && a.color === b.color
  && !!a.collapsed === !!b.collapsed);
const groupIdOf = (tab) => tab?.group?.id || null;

// The tab with `group` (null: none). The key goes when there is none, so the
// synced JSON carries nothing for an ungrouped tab.
function withGroup(tab, group) {
  if (sameGroup(tab.group, group)) return tab;
  if (group) return { ...tab, group };
  const { group: _gone, ...rest } = tab;
  return rest;
}

// The list as it is kept: pinned tabs first (a stable partition, so pinning
// a tab lands it after the pinned ones), no group on a pinned tab or a
// malformed one, a group's members one run (a group met in several runs is
// the longest run's, the first on a tie, and the other runs' tabs leave it),
// and every member the look of the run's first. Always a new array.
export function normalizeTabs(tabs) {
  const list = Array.isArray(tabs) ? tabs : [];
  const ordered = [...list.filter((tab) => tab.pinned), ...list.filter((tab) => !tab.pinned)];
  const looks = ordered.map((tab) => (tab.pinned ? null : cleanGroup(tab.group)));
  // each group's longest run, as [start, end)
  const kept = new Map();
  for (let i = 0; i < ordered.length;) {
    const id = looks[i]?.id;
    let j = i + 1;
    while (id && j < ordered.length && looks[j]?.id === id) j++;
    if (id && (!kept.has(id) || j - i > kept.get(id)[1] - kept.get(id)[0])) kept.set(id, [i, j]);
    i = j;
  }
  return ordered.map((tab, i) => {
    const run = kept.get(looks[i]?.id);
    return withGroup(tab, run && i >= run[0] && i < run[1] ? looks[run[0]] : null);
  });
}

// Each group's tabs into one pane of this browser's arrangement (tabLayout.js),
// the pane its first tab is in: another device grouping tabs this browser
// keeps in both panes, or a phone's one strip, would otherwise leave a part
// of the group in each pane, and the list's order (the panes', one after the
// other) would split it. What is on screen stays (moveTabs); nothing synced
// changes. The panes' order among their tabs is reconcile's to set after.
export function gatherPanes(layout, tabs) {
  let next = layout;
  for (const group of groupsIn(tabs)) {
    const [first, ...rest] = membersOf(tabs, group.id);
    const home = groupOf(next, first);
    const strays = rest.filter((id) => groupOf(next, id) && groupOf(next, id) !== home);
    if (home && strays.length) next = moveTabs(next, strays, home.id);
  }
  return next;
}

// A strip's tabs as it draws them: a tab alone, or a group with its run.
export function stripRuns(tabs) {
  const items = [];
  for (const tab of tabs) {
    const last = items[items.length - 1];
    if (tab.group && last?.group?.id === tab.group.id) last.tabs.push(tab);
    else items.push(tab.group ? { group: tab.group, tabs: [tab] } : { tab });
  }
  return items;
}

// The groups among `tabs`, each once, in order.
export function groupsIn(tabs) {
  const seen = new Map();
  for (const tab of tabs) if (tab.group && !seen.has(tab.group.id)) seen.set(tab.group.id, tab.group);
  return [...seen.values()];
}

export const membersOf = (tabs, groupId) => tabs.filter((tab) => groupIdOf(tab) === groupId).map((tab) => tab.id);

// The colour the fewest of the strip's groups have, the first such in
// Chrome's order.
export function leastUsedColor(tabs) {
  const uses = new Map(COLORS.map((color) => [color, 0]));
  for (const group of groupsIn(tabs)) uses.set(group.color, (uses.get(group.color) || 0) + 1);
  return COLORS.reduce((best, color) => (uses.get(color) < uses.get(best) ? color : best), COLORS[0]);
}

// A tab taken out of its group, standing right after the group's run so the
// group stays one.
export function leaveGroup(tabs, tabId) {
  const tab = tabs.find((t) => t.id === tabId);
  if (!tab?.group) return tabs;
  const rest = tabs.filter((t) => t.id !== tabId);
  const last = rest.findLastIndex((t) => groupIdOf(t) === tab.group.id);
  const next = [...rest];
  next.splice(last < 0 ? tabs.indexOf(tab) : last + 1, 0, withGroup(tab, null));
  return next;
}

// A new group of one: the tab (out of any group it was in) where it stands,
// in the colour its strip uses least. `strip`: the tabs the colour is
// counted among.
export function groupTab(tabs, tabId, groupId, strip = tabs) {
  const tab = tabs.find((t) => t.id === tabId);
  if (!tab || tab.pinned) return tabs;
  const group = { id: groupId, title: "", color: leastUsedColor(strip) };
  return leaveGroup(tabs, tabId).map((t) => (t.id === tabId ? withGroup(t, group) : t));
}

// A tab into a group, at the end of its run; a collapsed group opens, so
// the tab is not lost from sight.
export function joinGroup(tabs, tabId, groupId) {
  const tab = tabs.find((t) => t.id === tabId);
  const group = tabs.find((t) => groupIdOf(t) === groupId)?.group;
  if (!tab || tab.pinned || !group || groupIdOf(tab) === groupId) return tabs;
  const rest = leaveGroup(tabs, tabId).filter((t) => t.id !== tabId);
  const next = [...rest];
  next.splice(rest.findLastIndex((t) => groupIdOf(t) === groupId) + 1, 0, withGroup(tab, group));
  return group.collapsed ? setGroup(next, groupId, { collapsed: false }) : next;
}

// A group's look changed: title, colour, collapsed.
export function setGroup(tabs, groupId, patch) {
  return tabs.map((tab) => (groupIdOf(tab) === groupId ? withGroup(tab, cleanGroup({ ...tab.group, ...patch })) : tab));
}

// The group gone, its tabs where they stand.
export const ungroup = (tabs, groupId) => tabs.map((tab) => (groupIdOf(tab) === groupId ? withGroup(tab, null) : tab));

// What a strip opens as its group collapses: undefined when the strip's
// active tab is not in the group (nothing to open), else the nearest tab
// outside it, after the run first and then before it, one in sight (not in
// another collapsed group) before one that is not; null when every tab is in
// the group (the library).
export function collapseHeir(strip, groupId, activeId) {
  const at = strip.findIndex((tab) => tab.id === activeId);
  if (at < 0 || groupIdOf(strip[at]) !== groupId) return undefined;
  const order = [...strip.slice(at + 1), ...strip.slice(0, at).reverse()].filter((tab) => groupIdOf(tab) !== groupId);
  return (order.find((tab) => !tab.group?.collapsed) || order[0])?.id ?? null;
}

const sameStrip = (a, b) => a.length === b.length && a.every((tab, i) => tab.id === b[i].id && sameGroup(tab.group, b[i].group));

// A drag in a strip. `dragged`: a tab id, or {group: id} for a chip.
// `target`: the tab id it is over, {group: id} for a chip, null for the
// strip's empty end. A tab takes the place of the tab it is over, as the
// strip always reordered, and that tab's group: it joins a group by passing
// over one of its tabs and leaves its own by passing over a tab outside it,
// unless it is its group's only tab, which carries the group along. A
// collapsed group, which shows one tab at most, is passed whole and never
// joined: the tab would vanish from under the drag. Over a chip it stands
// just before that group, over the end at the end, ungrouped either way. A
// chip carries its group past a tab, or past another group whole, never
// into it. Pinned and unpinned tabs never trade places. The strip in its
// new order, or null when nothing changes.
export function moveInStrip(strip, dragged, target) {
  const byId = (id) => strip.find((tab) => tab.id === id);
  const over = typeof target === "string" ? byId(target) : null;
  if (typeof target === "string" && !over) return null;
  let next;
  if (typeof dragged === "string") {
    const tab = byId(dragged);
    if (!tab) return null;
    if (over) {
      if (over === tab || !!over.pinned !== !!tab.pinned) return null;
      const own = tab.group && !strip.some((t) => t !== tab && groupIdOf(t) === tab.group.id) ? tab.group : null;
      next = strip.filter((t) => t !== tab);
      if (over.group?.collapsed) {
        const first = next.findIndex((t) => groupIdOf(t) === over.group.id);
        const last = next.findLastIndex((t) => groupIdOf(t) === over.group.id);
        next.splice(strip.indexOf(tab) < strip.indexOf(over) ? last + 1 : first, 0, withGroup(tab, own));
      } else next.splice(strip.indexOf(over), 0, withGroup(tab, over.group || own));
    } else {
      if (tab.pinned) return null;
      next = strip.filter((t) => t !== tab);
      const at = target ? next.findIndex((t) => groupIdOf(t) === target.group) : next.length;
      if (at < 0) return null;
      next.splice(at, 0, withGroup(tab, null));
    }
  } else {
    const id = dragged?.group;
    const run = strip.filter((tab) => groupIdOf(tab) === id);
    if (!run.length || over?.pinned) return null;
    const anchor = over ? groupIdOf(over) : target?.group ?? null;
    if (target && anchor === id) return null;
    next = strip.filter((tab) => groupIdOf(tab) !== id);
    let at = next.length;
    if (target) {
      // the tab it is over, or the whole group it is over
      const span = next.reduce((s, tab, i) => ((anchor ? groupIdOf(tab) === anchor : tab === over) ? [...s, i] : s), []);
      if (!span.length) return null;
      // a run carried right lands after what it passed, one carried left before it
      at = strip.indexOf(next[span[0]]) > strip.indexOf(run[0]) ? span[span.length - 1] + 1 : span[0];
    }
    next.splice(at, 0, ...run);
  }
  return sameStrip(strip, next) ? null : next;
}

// The groups a strip's drag gave its tabs, onto the list.
export function takeGroups(tabs, strip) {
  const byId = new Map(strip.map((tab) => [tab.id, tab]));
  return tabs.map((tab) => (byId.has(tab.id) ? withGroup(tab, byId.get(tab.id).group || null) : tab));
}

// A tab moved into another pane, `paneTabIds` that pane's tabs as they now
// stand: it joins the group it landed inside (both neighbours in it), else
// keeps its own beside another of its tabs or as the group's only tab, else
// is in none.
export function landTab(tabs, tabId, paneTabIds) {
  const byId = new Map(tabs.map((tab) => [tab.id, tab]));
  const tab = byId.get(tabId);
  if (!tab) return tabs;
  const at = paneTabIds.indexOf(tabId);
  const left = at > 0 ? byId.get(paneTabIds[at - 1])?.group : null;
  const right = at >= 0 ? byId.get(paneTabIds[at + 1])?.group : null;
  const own = tab.group;
  const inside = left && left.id === right?.id ? left : null;
  const keeps = own && (left?.id === own.id || right?.id === own.id || !tabs.some((t) => t !== tab && groupIdOf(t) === own.id));
  const group = inside || (keeps ? own : null);
  return tabs.map((t) => (t === tab ? withGroup(t, group) : t));
}
