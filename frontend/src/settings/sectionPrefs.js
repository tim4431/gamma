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
    "PDF pages": ["pdfDarkPage"],
    Library: ["recentThumbs", "fileLabels"],
    "Sync status": ["syncPillScope"],
    Tours: ["suggestTours"],
  }),
  reading: sections({
    PDFs: ["embAnnots", "oaFallback", "metaAutoFetch", "pdfSaveLocal"],
    Notes: ["backlinksVisible"],
    "Search opens as": ["searchDetailsHome", "searchDetailsPaper"],
  }),
  translation: sections({
    Language: ["language"],
    "Viewer & selection": ["translateEnabled", "translateLang", "selTranslate", "selTranslateAuto"],
    Speed: ["translateEffort", "translateParallel"],
  }),
  keyboard: sections({
    Shortcuts: ["keybindings"],
    "Built in": ["enterNewNote"],
  }),
  connections: sections({
    "Connection check": ["aiLoginCheck"],
  }),
  assistant: sections({
    Chat: ["chatEffort", "chatSpeed", "chatImgAutoClear"],
    Prompts: ["chatSystem", "metaPrompt", "citePrompt", "agentSystem"],
    "Context size": ["chatContextChars", "metaContextChars", "multiContextChars"],
  }),
  tools: sections({
    Tools: ["agentEnabled", "agentPerms", "fetchInBackground", "delegateReads"],
    "Tool limits": ["toolRounds", "agentReadChars"],
  }),
});
