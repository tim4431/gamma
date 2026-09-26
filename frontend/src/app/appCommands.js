// The commands the whole app answers to, wherever focus is (docs/dev/hotkeys.md):
// App.jsx's one window keydown listener dispatches this catalog
// (shared/lib/hotkeys.js) with a ctx of handles it refreshes every render;
// the command palette (Ctrl+Shift+P) lists the same entries, and Settings →
// Keyboard rebinds them. Only the long-standing keys, F2 and Ctrl+, have defaults;
// the rest are palette entries until the account gives them a chord.
// ctx: { shareMode, homeMode, hasPage, hasPdf, readOnly, search(all),
// palette(prefix), back(), undo(redo), renameTitle(), toggleChat(),
// togglePdf(), toggleNotes(), openSettings(pane), exportAs(format),
// downloadPdf(), importDialog(), newPage(), share(), metadata(), attach(),
// reportProblem() }.
import { t } from "../shared/i18n/i18n.js";

export const GROUP_NAVIGATION = t("Navigation");
export const GROUP_PAGE = t("Page");
export const GROUP_VIEW = t("View");
export const GROUP_LIBRARY = t("Library");

const inField = (el) => !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
const cmd = (id, label, group, keys, run, extra = {}) => ({ id, label, group, keys, run, ...extra });

// The open dialog on top, if any: every modal (Settings and its sub-dialogs,
// Import, Export, the palette, the confirm box…) sits in a `.reportOverlay`,
// a nested one later in the DOM than its parent.
export function topDialog() {
  const all = document.querySelectorAll(".reportOverlay");
  return all.length ? all[all.length - 1] : null;
}
// While a dialog is open it owns the keys: only the commands whose
// `inDialog(dialog)` holds dispatch. The rest act on the page behind it,
// out of sight: Ctrl+Z would undo a note from inside Settings.
export function liveAppCommands() {
  const dialog = topDialog();
  return dialog ? APP_COMMANDS.filter((cmd) => cmd.inDialog?.(dialog)) : APP_COMMANDS;
}
// Ctrl+F inside a dialog: the dialog's own search box (marked `data-find`),
// else the browser's find. With several (Settings' search above the Keyboard
// pane's filter) the innermost — the last — comes first, and pressing again
// from one goes to the one before it.
function findInDialog(dialog) {
  const boxes = [...dialog.querySelectorAll("[data-find]")].filter((el) => el.getClientRects().length && !el.closest("[inert]"));
  if (!boxes.length) return false;
  const at = boxes.indexOf(document.activeElement);
  const box = boxes[(at < 0 ? boxes.length : at) - 1] || boxes[boxes.length - 1];
  box.focus();
  box.select?.();
  return true;
}
const inPalette = (dialog) => dialog.classList.contains("quickOpenOverlay");

export const APP_COMMANDS = [
  // The built-in search over notes, highlights and PDF text — not the
  // browser's find. In a dialog it goes to the dialog's search box; focus in
  // the chat window leaves the key to ChatDock's find-in-chat; on the home
  // library the plain key goes to the listing's search box (only rendered
  // there — DOM presence stands in for homeMode) and Ctrl+Shift+F still
  // opens the full panel.
  cmd("app.search", t("Search"), GROUP_NAVIGATION, "Mod-f", (c) => {
    const dialog = topDialog();
    if (dialog) return findInDialog(dialog);
    if (document.activeElement?.closest?.(".chatPanel")) return false;
    return c.search(false);
  }, { inDialog: () => true }),
  cmd("app.searchAll", t("Search everything"), GROUP_NAVIGATION, "Mod-Shift-f", (c) => {
    if (document.activeElement?.closest?.(".chatPanel")) return false;
    return c.search(true);
  }),
  // A share view has no library to pick from. In the open palette the two
  // keys switch between pages and commands, or close it.
  cmd("app.quickOpen", t("Go to page"), GROUP_NAVIGATION, "Mod-p", (c) => { c.palette(""); }, { when: (c) => !c.shareMode, inDialog: inPalette }),
  cmd("app.commandPalette", t("Command palette"), GROUP_NAVIGATION, "Mod-Shift-p", (c) => { c.palette(">"); }, { palette: false, inDialog: inPalette }),
  cmd("app.back", t("Back to where you were"), GROUP_NAVIGATION, "Alt-ArrowLeft", (c) => { c.back(); }),
  cmd("app.settings", t("Open settings"), GROUP_NAVIGATION, "Mod-,", (c) => { c.openSettings(); }, { when: (c) => !c.shareMode }),
  cmd("app.keyboardSettings", t("Keyboard shortcuts…"), GROUP_NAVIGATION, null, (c) => { c.openSettings("keyboard"); }, { when: (c) => !c.shareMode }),
  cmd("app.workspaces", t("Workspaces…"), GROUP_NAVIGATION, null, (c) => { c.openSettings("workspaces"); }, { when: (c) => !c.shareMode }),

  // The page's one undo history — from a block editor too (it has no
  // history of its own). Other inputs keep the browser's own undo, so the
  // command declines there.
  cmd("app.undo", t("Undo"), GROUP_PAGE, "Mod-z", (c) => c.undo(false)),
  cmd("app.redo", t("Redo"), GROUP_PAGE, ["Mod-y", "Mod-Shift-z"], (c) => c.undo(true)),
  cmd("app.renameTitle", t("Rename page"), GROUP_PAGE, "F2", (c) => {
    if (inField(document.activeElement) && !document.activeElement.closest(".cm-editor")) return false;
    c.renameTitle();
  }, { when: (c) => c.hasPage && !c.readOnly }),
  cmd("app.share", t("Share this page…"), GROUP_PAGE, null, (c) => { c.share(); }, { when: (c) => c.hasPage && !c.shareMode }),
  cmd("app.metadata", t("Paper metadata…"), GROUP_PAGE, null, (c) => { c.metadata(); }, { when: (c) => c.hasPdf }),
  cmd("app.attach", t("Attach a PDF…"), GROUP_PAGE, null, (c) => { c.attach(); }, { when: (c) => c.hasPage && !c.hasPdf && !c.readOnly }),
  cmd("app.exportAnnotated", t("Export the annotated PDF"), GROUP_PAGE, null, (c) => { c.exportAs("pdf"); }, { when: (c) => c.hasPdf }),
  cmd("app.exportNotesPdf", t("Export the notes as a PDF"), GROUP_PAGE, null, (c) => { c.exportAs("notespdf"); }, { when: (c) => c.hasPage }),
  cmd("app.exportMarkdown", t("Export the notes as Markdown"), GROUP_PAGE, null, (c) => { c.exportAs("markdown"); }, { when: (c) => c.hasPage }),
  cmd("app.downloadPdf", t("Download the PDF file"), GROUP_PAGE, null, (c) => { c.downloadPdf(); }, { when: (c) => c.hasPdf }),

  cmd("app.toggleChat", t("Show or hide the chat"), GROUP_VIEW, null, (c) => { c.toggleChat(); }, { when: (c) => !c.shareMode }),
  cmd("app.togglePdf", t("Show or hide the PDF"), GROUP_VIEW, null, (c) => { c.togglePdf(); }, { when: (c) => c.hasPdf }),
  cmd("app.toggleNotes", t("Show or hide the notes"), GROUP_VIEW, null, (c) => { c.toggleNotes(); }, { when: (c) => c.hasPdf }),

  cmd("app.newPage", t("New page"), GROUP_LIBRARY, null, (c) => { c.newPage(); }, { when: (c) => !c.shareMode }),
  cmd("app.import", t("Import…"), GROUP_LIBRARY, null, (c) => { c.importDialog(); }, { when: (c) => !c.shareMode }),
  cmd("app.exportObsidian", t("Export the library as an Obsidian vault"), GROUP_LIBRARY, null, (c) => { c.exportAs("obsidian"); }, { when: (c) => !c.shareMode }),
  cmd("app.exportGamma", t("Export the library as a Gamma backup"), GROUP_LIBRARY, null, (c) => { c.exportAs("gamma"); }, { when: (c) => !c.shareMode }),
  cmd("app.reportProblem", t("Report a problem…"), GROUP_LIBRARY, null, (c) => { c.reportProblem(); }, { when: (c) => !c.shareMode }),
];
