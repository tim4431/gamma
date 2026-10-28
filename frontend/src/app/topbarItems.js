// The topbar's configurable items (Settings → Appearance › Top bar), in the
// bar's default order, and where each one goes. Pure (no React, no DOM), so
// node tests it (tests/topbarItems.test.mjs): App places the items with
// TOPBAR.place, the pane draws one row per item and drags them into order.
//
// The `topbar` preference (app/prefDefs.js) is a bar layout
// (shared/model/barLayout.js), { order, modes }: the order the items stand
// in, and a mode per item:
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
// Home, Back, the tabs and Account & settings are not items and keep their
// places (Account last): the account menu is where the others go, and the
// way back to this setting.
import { T } from "../shared/i18n/locales.js";
import { defineBar } from "../shared/model/barLayout.js";

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

// The bar (shared/model/barLayout.js): the preference's default and codec,
// the edits its Settings rows make, and where each item goes.
export const TOPBAR = defineBar(TOPBAR_ITEMS, {
  tips: { hidden: T("A row of the account menu instead (More, on a phone)") },
});
