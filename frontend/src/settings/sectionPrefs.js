// Which account preferences each settings section holds, per pane, so a
// section's tag (Section's `prefs`, settings/syncState.js) reflects only its
// own settings: one change spins only the sections that hold it. Pure, so
// node tests it (tests/sectionPrefs.test.mjs) against PREFS and the panes'
// sources — a renamed preference fails there instead of silently losing its
// tag.
import { ACCOUNT, PREFS } from "../app/prefDefs.js";

// The names as given, after checking each is an account-scoped entry of
// PREFS; anything else throws.
export function accountPrefs(names) {
  for (const name of names) {
    if (!PREFS[name]) throw new Error(`settings section: unknown preference "${name}"`);
    if (PREFS[name].scope !== ACCOUNT) throw new Error(`settings section: "${name}" is not an account preference`);
  }
  return Object.freeze([...names]);
}

const sections = (table) => Object.freeze(Object.fromEntries(
  Object.entries(table).map(([title, names]) => [title, accountPrefs(names)])));

// pane → section title → preference names. Every `<Section scope="account">`
// passes its entry here as `prefs`.
export const SECTION_PREFS = Object.freeze({
  appearance: sections({
    Theme: ["theme"],
    Language: ["language"],
    "PDF pages": ["pdfDarkPage"],
    Library: ["recentThumbs", "fileLabels"],
    Tours: ["suggestTours"],
  }),
  reading: sections({
    PDFs: ["embAnnots", "oaFallback", "metaAutoFetch", "pdfSaveLocal"],
    Translation: ["translateEnabled", "translateLang", "selTranslate", "selTranslateAuto", "translateEffort", "translateParallel"],
    Notes: ["enterNewNote"],
    "Search opens as": ["searchDetailsHome", "searchDetailsPaper"],
  }),
  keyboard: sections({
    Shortcuts: ["keybindings"],
  }),
  sync: sections({
    "Sync status": ["syncPillScope"],
  }),
  connections: sections({
    "Connection check": ["aiLoginCheck"],
  }),
  assistant: sections({
    Chat: ["chatEffort", "chatImgAutoClear"],
    Tools: ["agentEnabled", "agentPerms"],
  }),
  advanced: sections({
    "Tool limits": ["toolRounds", "agentReadChars"],
    "Context size": ["chatContextChars", "metaContextChars", "multiContextChars"],
  }),
  prompts: sections({
    Prompts: ["chatSystem", "metaPrompt", "citePrompt", "agentSystem"],
  }),
});
