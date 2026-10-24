// The topbar's configurable items (Settings → Appearance › Top bar), in the
// bar's order, and where each one goes. Pure (no React, no DOM), so node
// tests it (tests/topbarItems.test.mjs): App places the items with
// topbarPlaces, the pane draws one row per item.
//
// The `topbar` preference (app/prefDefs.js) holds a mode per item:
// - "always": on the bar;
// - "auto": on the bar while it has something to say (`active`: a task
//   running, failed or waiting to be downloaded; an unread Inbox item),
//   else a row of the account menu. The sync pill's auto is "on the pages
//   that sync", which the pill judges itself (collaboration/MirrorPopover.jsx),
//   so App passes it as active;
// - "hidden": a row of the account menu instead (the More sheet on a
//   phone): one click further, never gone.
// An open popover keeps its button, so an auto item does not vanish under
// the pointer while its news is read. While a tour runs or a hint is
// offered every item is on the bar, since their steps point at them. The
// phone's bottom bar has no room for the items marked `phone: false`: they
// are rows of its More sheet whatever their mode.
//
// Home, Back, the tabs and Account & settings are not items: the account
// menu is where the others go, and the way back to this setting.
import { T } from "../shared/i18n/locales.js";

export const TOPBAR_MODES = ["always", "auto", "hidden"];

// Each item: `id` (its key in the preference), `popover` (App's
// openPopover name for its panel), `label` and `hint` (its Settings row),
// `keywords` (English synonyms for the settings search), `modes` (the
// choices offered, the first its default), and per mode its own word
// (`labels`, where the generic Shown / Hidden would read wrong; every item
// offering auto names it) and tooltip (`tips`).
export const TOPBAR_ITEMS = Object.freeze([
  {
    id: "add", popover: "add", label: T("Add"),
    hint: T("New pages and notebooks, uploads, a paper by its link."),
    keywords: "plus new page notebook upload URL DOI arXiv",
    modes: ["always", "hidden"],
  },
  {
    id: "tasks", popover: "downloads", label: T("Background tasks"),
    hint: T("Exports, backups, imports, downloads and uploads."),
    keywords: "auto tasks progress activity spinner",
    modes: ["always", "auto", "hidden"], phone: false,
    labels: { auto: T("When active") },
    tips: { auto: T("While a task runs, or one failed or has a file to download") },
  },
  {
    id: "search", popover: "search", label: T("Search"),
    hint: T("Its shortcut opens it either way."),
    keywords: "find magnifier",
    modes: ["always", "hidden"],
  },
  {
    id: "share", popover: "share", label: T("Share"),
    hint: T("The open page's link, or the open folder's."),
    keywords: "link sharing",
    modes: ["always", "hidden"],
  },
  {
    id: "sync", popover: "mirror", label: T("Sync pill"),
    hint: T("A page's sync with Gamma Cloud, or a clone's with its server."),
    keywords: "sync status publish published pages Gamma Cloud clone every page",
    modes: ["auto", "always", "hidden"],
    labels: { auto: T("Synced pages"), always: T("Every page") },
    tips: {
      auto: T("Only on the pages that sync with Gamma Cloud; a clone of another server syncs every page"),
      always: T("On every page of a workspace that syncs some"),
    },
  },
  {
    id: "inbox", popover: "inbox", label: T("Inbox"),
    hint: T("Mentions, replies, shares and invitations."),
    keywords: "auto bell unread notifications",
    modes: ["always", "auto", "hidden"], phone: false,
    labels: { auto: T("When unread") },
    tips: { auto: T("Only while something is unread") },
  },
  {
    id: "view", popover: "menu", label: T("View menu"),
    hint: T("Windows, undo and redo, the palettes, import and export."),
    keywords: "hamburger windows import export",
    modes: ["always", "hidden"], phone: false,
  },
]);

export const TOPBAR_ITEM = Object.freeze(Object.fromEntries(TOPBAR_ITEMS.map((item) => [item.id, item])));

export const DEFAULT_TOPBAR = Object.freeze(Object.fromEntries(TOPBAR_ITEMS.map((item) => [item.id, item.modes[0]])));

const MODE_LABELS = { always: T("Shown"), hidden: T("Hidden") };
const MODE_TIPS = { hidden: T("A row of the account menu instead (More, on a phone)") };

// A mode's word and tooltip in an item's Settings row (untranslated: the
// row's Segmented translates them).
export const modeLabel = (item, mode) => item.labels?.[mode] || MODE_LABELS[mode] || mode;
export const modeTip = (item, mode) => item.tips?.[mode] || MODE_TIPS[mode] || "";

// The preference as stored, back to a mode for every item: an unknown id
// is dropped, a missing one or a mode its item does not offer takes the
// item's default, so a value written by an older or newer Gamma applies
// what it can. Anything but an object is undefined (the codec's default).
export function normalizeTopbar(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return Object.fromEntries(TOPBAR_ITEMS.map((item) => [item.id, item.modes.includes(value[item.id]) ? value[item.id] : item.modes[0]]));
}

export function isDefaultTopbar(value) {
  const modes = normalizeTopbar(value) || DEFAULT_TOPBAR;
  return TOPBAR_ITEMS.every((item) => modes[item.id] === DEFAULT_TOPBAR[item.id]);
}

// Whether the bar shows an item in `mode`: always; auto while it has
// something to say or its popover is open; hidden never.
export function barShows(mode, { active = false, open = false } = {}) {
  return mode === "always" || (mode === "auto" && (active || open));
}

// Where each item goes here, {id: "bar" | "menu"}: on the bar, or a row of
// the account menu (the More sheet on a phone). `modes` is the preference;
// `phone`, the compact layout; `guide`, a tour or hint on screen; `open`,
// the open popover's name; `active`, the items with something to say
// ({id: bool}). Whether an item applies here at all (Share with no page
// or folder open, a guest's Inbox) is App's to say; this says where it
// would go.
export function topbarPlaces(modes, { phone = false, guide = false, open = null, active = {} } = {}) {
  const prefs = normalizeTopbar(modes) || DEFAULT_TOPBAR;
  return Object.fromEntries(TOPBAR_ITEMS.map((item) => {
    if (phone && item.phone === false) return [item.id, "menu"];
    const bar = guide || barShows(prefs[item.id], { active: !!active[item.id], open: open === item.popover });
    return [item.id, bar ? "bar" : "menu"];
  }));
}
