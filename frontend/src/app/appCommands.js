// The commands the whole app answers to, wherever focus is (docs/dev/hotkeys.md):
// App.jsx's one window keydown listener dispatches this catalog
// (shared/lib/hotkeys.js) with a ctx of handles it refreshes every render;
// the command palette lists the same entries, and Settings → Keyboard
// rebinds them. ctx: { shareMode, homeMode, hasPage, hasPdf, readOnly,
// search(all), palette(prefix), back(), undo(redo), renameTitle(),
// toggleChat(), togglePdf(), toggleNotes(), openSettings(pane) }.
import { t } from "../shared/i18n/i18n.js";

export const GROUP_NAVIGATION = t("Navigation");
export const GROUP_PAGE = t("Page");
export const GROUP_VIEW = t("View");

const inField = (el) => !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);

export const APP_COMMANDS = [
  {
    // The built-in search over notes, highlights and PDF text — not the
    // browser's find. Focus in the chat window leaves the key to
    // ChatDock's find-in-chat; on the home library the plain key goes to
    // the listing's search box (only rendered there — DOM presence stands
    // in for homeMode) and Ctrl+Shift+F still opens the full panel.
    id: "app.search", label: t("Search"), group: GROUP_NAVIGATION, keys: "Mod-f",
    run: (c) => {
      if (document.activeElement?.closest?.(".chatPanel")) return false;
      return c.search(false);
    },
  },
  {
    id: "app.searchAll", label: t("Search everything"), group: GROUP_NAVIGATION, keys: "Mod-Shift-f",
    run: (c) => {
      if (document.activeElement?.closest?.(".chatPanel")) return false;
      return c.search(true);
    },
  },
  {
    // A share view has no library to pick from.
    id: "app.quickOpen", label: t("Go to page"), group: GROUP_NAVIGATION, keys: "Mod-p",
    when: (c) => !c.shareMode,
    run: (c) => { c.palette(""); },
  },
  {
    id: "app.commandPalette", label: t("Command palette"), group: GROUP_NAVIGATION, keys: "Mod-Shift-p", palette: false,
    run: (c) => { c.palette(">"); },
  },
  {
    id: "app.back", label: t("Back to where you were"), group: GROUP_NAVIGATION, keys: "Alt-ArrowLeft",
    run: (c) => { c.back(); },
  },
  {
    // The page's one undo history — from a block editor too (it has no
    // history of its own). Other inputs keep the browser's own undo, so
    // the command declines there.
    id: "app.undo", label: t("Undo"), group: GROUP_PAGE, keys: "Mod-z",
    run: (c) => c.undo(false),
  },
  {
    id: "app.redo", label: t("Redo"), group: GROUP_PAGE, keys: ["Mod-y", "Mod-Shift-z"],
    run: (c) => c.undo(true),
  },
  {
    id: "app.renameTitle", label: t("Rename page"), group: GROUP_PAGE, keys: "F2",
    when: (c) => c.hasPage && !c.readOnly,
    run: (c) => { if (inField(document.activeElement) && !document.activeElement.closest(".cm-editor")) return false; c.renameTitle(); },
  },
  {
    id: "app.toggleChat", label: t("Show or hide the chat"), group: GROUP_VIEW, keys: "Mod-j",
    when: (c) => !c.shareMode,
    run: (c) => { c.toggleChat(); },
  },
  {
    id: "app.togglePdf", label: t("Show or hide the PDF"), group: GROUP_VIEW, keys: "Mod-\\",
    when: (c) => c.hasPdf,
    run: (c) => { c.togglePdf(); },
  },
  {
    id: "app.toggleNotes", label: t("Show or hide the notes"), group: GROUP_VIEW, keys: null,
    when: (c) => c.hasPdf,
    run: (c) => { c.toggleNotes(); },
  },
  {
    id: "app.settings", label: t("Open settings"), group: GROUP_VIEW, keys: "Mod-,",
    when: (c) => !c.shareMode,
    run: (c) => { c.openSettings(); },
  },
  {
    id: "app.keyboardSettings", label: t("Keyboard shortcuts…"), group: GROUP_VIEW, keys: null,
    when: (c) => !c.shareMode,
    run: (c) => { c.openSettings("keyboard"); },
  },
];
