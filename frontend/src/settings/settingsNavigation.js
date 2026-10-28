// Settings pane names: what App needs to open the dialog at a pane (the
// search catalog is settingsSearch.js).

// sessionStorage: the Settings pane to reopen after a reload that leaves the
// page (a language change, a round trip through the account server).
export const REOPEN_SETTINGS_KEY = "gamma-reopen-settings";
// Old entry points stay valid while everyday preferences use five destinations.
const PANE_ALIASES = {
  general: "appearance", papers: "appearance", viewer: "reading", notes: "keyboard", search: "reading",
  context: "assistant", "ai-advanced": "assistant", prompts: "assistant", library: "appearance",
  workspace: "workspaces", advanced: "diagnostics", sync: "account",
  // The markup strip's More → Handwriting settings…: Reading & editing, at
  // the Handwriting section (SettingsDialog jumps to its first row).
  handwriting: "reading",
  // Customize… on a right-click or held finger on the top bar's background
  // or the viewer strip, and the strip's ⋯ menu's Customize…: Appearance,
  // at the Top bar or the Viewer tools section.
  topbar: "appearance", "viewer-tools": "appearance",
};
export const resolveSettingsPane = (pane) => PANE_ALIASES[pane] || pane;
