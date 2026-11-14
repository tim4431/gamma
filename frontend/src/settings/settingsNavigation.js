// Settings pane names: what App needs to open the dialog at a pane (the
// search catalog is settingsSearch.js).
import { T } from "../shared/i18n/locales.js";

// sessionStorage: the Settings pane to reopen after a reload that leaves the
// page (a language change, a round trip through the account server).
export const REOPEN_SETTINGS_KEY = "gamma-reopen-settings";
// Section entry points: a name that opens a pane at one of its sections, its
// pane and the label of the row SettingsDialog jumps to (the row's
// data-setting). Every other name is a pane's own.
// (The markup strip's More → Handwriting settings… names the Handwriting
// pane itself, `handwriting`.)
const SECTIONS = {
  // Customize… on a right-click or held finger on the top bar's background
  // or the viewer strip, and the strip's ⋯ menu's Customize…: Toolbars, at
  // the Top bar or the Viewer tools section.
  topbar: ["toolbars", T("Top bar")],
  // Edit highlighters… (the selection popup's fold, a strip highlighter's
  // menu): Toolbars, at the Text highlighters section.
  highlighters: ["toolbars", T("Text highlighters")],
  "viewer-tools": ["toolbars", T("Viewer tools")],
};
export const resolveSettingsPane = (pane) => SECTIONS[pane]?.[0] || pane;
// The untranslated label of the row a section entry point jumps to, or "".
export const settingsSection = (pane) => SECTIONS[pane]?.[1] || "";
