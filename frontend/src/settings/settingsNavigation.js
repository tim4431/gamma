// Settings pane names: what App needs to open the dialog at a pane (the
// search catalog is settingsSearch.js).
import { T } from "../shared/i18n/locales.js";

// sessionStorage: the Settings pane to reopen after a reload that leaves the
// page (a language change, a round trip through the account server).
export const REOPEN_SETTINGS_KEY = "gamma-reopen-settings";
// Section entry points: a name that opens a pane at one of its sections, its
// pane and the label of the row SettingsDialog jumps to (the row's
// data-setting). Every other name is a pane's own.
const SECTIONS = {
  // The markup strip's More → Handwriting settings…: Reading & editing, at
  // the Handwriting section's first row.
  handwriting: ["reading", T("Reset presets")],
  // Customize… on a right-click or held finger on the top bar's background
  // or the viewer strip, and the strip's ⋯ menu's Customize…: Appearance,
  // at the Top bar or the Viewer tools section.
  topbar: ["appearance", T("Top bar")],
  "viewer-tools": ["appearance", T("Viewer tools")],
};
export const resolveSettingsPane = (pane) => SECTIONS[pane]?.[0] || pane;
// The untranslated label of the row a section entry point jumps to, or "".
export const settingsSection = (pane) => SECTIONS[pane]?.[1] || "";
