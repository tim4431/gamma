// A bar's layout (docs/dev/settings.md "Bars: order and modes"): the order
// its items stand in and a mode per item, the one model the top bar
// (app/topbarItems.js) and the viewer's side strip (app/viewerToolItems.js)
// share. Pure (no React, no DOM), so node tests it
// (tests/barLayout.test.mjs).
//
// A bar is a registry, its items in their default order. Each item has an
// `id` (its key in the preference), `modes` (the choices offered, the first
// its default), and may carry its own word and tooltip per mode (`labels`,
// `tips`) where the bar's generic ones would read wrong. The preference is
// { order: [ids], modes: { id: mode } }; defineBar gives the codec that
// reads it back, the edits Settings makes, and where each item goes.
//
// The modes:
// - "always": on the bar;
// - "auto": on the bar while it has something to say (`active`) or its
//   popover is open, else where the hidden ones go;
// - "hidden": off the bar, a row of the bar's menu instead (the account
//   menu for the top bar, the ⋯ menu for the strip): one click further,
//   never gone.
import { T } from "../i18n/locales.js";

export const BAR_MODES = ["always", "auto", "hidden"];

const MODE_LABELS = { always: T("Shown"), hidden: T("Hidden") };

// Whether the bar shows an item in `mode`: always; auto while it has
// something to say or its popover is open; hidden never.
export function barShows(mode, { active = false, open = false } = {}) {
  return mode === "always" || (mode === "auto" && (active || open));
}

// `list` with `item` moved to the visible slot `to` (0 … the visible
// count: before the visible element at that index, or last) — the drop of
// a drag, and a key's step. The elements `isVisible` rejects keep their
// places between the visible ones. The same list when the slot is where
// the item already stands, or the item is not a visible one.
export function moveVisible(list, item, to, isVisible) {
  const visible = list.filter(isVisible);
  const from = visible.indexOf(item);
  if (from < 0 || to === from || to === from + 1) return list;
  const rest = list.filter((x) => x !== item);
  const restVisible = visible.filter((x) => x !== item);
  const j = to > from ? to - 1 : to;
  const at = j >= restVisible.length ? rest.length : rest.indexOf(restVisible[j]);
  return [...rest.slice(0, at), item, ...rest.slice(at)];
}

// The bar for a registry: `items` in their default order; `tips` the
// bar's own tooltip per mode (where its hidden items go).
export function defineBar(items, { tips = {} } = {}) {
  const byId = new Map(items.map((item) => [item.id, item]));
  const item = (id) => byId.get(id) || null;
  const DEFAULT = Object.freeze({
    order: Object.freeze(items.map((x) => x.id)),
    modes: Object.freeze(Object.fromEntries(items.map((x) => [x.id, x.modes[0]]))),
  });

  // The preference as stored, back to a whole layout: an unknown id is
  // dropped, a repeated one kept where it first stands, and an item the
  // order lacks (one a later release added) goes right after the item
  // before it in the registry, at the start when it is the first; a mode
  // its item does not offer takes the item's default. Anything but an
  // object is undefined (the codec's default).
  function normalize(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
    const order = [...new Set(Array.isArray(value.order) ? value.order.filter((id) => byId.has(id)) : [])];
    items.forEach((x, i) => {
      if (order.includes(x.id)) return;
      order.splice(i ? order.indexOf(items[i - 1].id) + 1 : 0, 0, x.id);
    });
    const stored = value.modes && typeof value.modes === "object" ? value.modes : {};
    const modes = Object.fromEntries(items.map((x) => [x.id, x.modes.includes(stored[x.id]) ? stored[x.id] : x.modes[0]]));
    return { order, modes };
  }
  const read = (value) => normalize(value) || DEFAULT;

  function isDefault(value) {
    const { order, modes } = read(value);
    return order.every((id, i) => id === DEFAULT.order[i]) && items.every((x) => modes[x.id] === DEFAULT.modes[x.id]);
  }

  // A mode the item does not offer leaves the layout as it is.
  function setMode(value, id, mode) {
    const layout = read(value);
    if (!item(id)?.modes.includes(mode)) return layout;
    return { ...layout, modes: { ...layout.modes, [id]: mode } };
  }

  // `id` moved to the visible slot `to` among the items `isVisible` keeps
  // (the rows a Settings list draws), the others keeping their places.
  function move(value, id, to, isVisible = () => true) {
    const layout = read(value);
    const order = moveVisible(layout.order, id, to, (x) => isVisible(byId.get(x)));
    return order === layout.order ? layout : { ...layout, order };
  }

  // Where each item goes, [{id, item, at: "bar" | "menu"}] in the layout's
  // order: on the bar, or a row of the bar's menu. `phone`, the compact
  // layout, whose bar has no room for the items marked `phone: false`;
  // `guide`, a tour or hint on screen, whose steps point at every item;
  // `open`, the open popover's name; `active`, the items with something to
  // say ({id: bool}); `pinned`, the items in use, which stay on the bar
  // whatever their mode. Whether an item applies here at all is the
  // caller's to say; this says where it would go.
  function place(value, { phone = false, guide = false, open = null, active = {}, pinned = {} } = {}) {
    const { order, modes } = read(value);
    return order.map((id) => {
      const x = byId.get(id);
      if (phone && x.phone === false) return { id, item: x, at: "menu" };
      const bar = guide || !!pinned[id] || barShows(modes[id], { active: !!active[id], open: open != null && open === x.popover });
      return { id, item: x, at: bar ? "bar" : "menu" };
    });
  }

  // A mode's word and tooltip in an item's Settings row (untranslated: the
  // row's Segmented translates them).
  const modeLabel = (x, mode) => x.labels?.[mode] || MODE_LABELS[mode] || mode;
  const modeTip = (x, mode) => x.tips?.[mode] || tips[mode] || "";

  return Object.freeze({ items, item, DEFAULT, normalize, read, isDefault, setMode, move, place, modeLabel, modeTip });
}
