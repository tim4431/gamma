// Settings search (settings/settingsNavigation.js): every entry must jump to
// something its pane really renders, or the jump finds nothing.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SETTINGS_SEARCH, searchSettings } from "../src/settings/settingsNavigation.js";

const SETTINGS = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/settings");
const source = (file) => fs.readFileSync(path.join(SETTINGS, file), "utf8");

// The sources each pane renders: its own file and the sections it pulls in.
const PANE_FILES = {
  appearance: ["SettingsAppearance.jsx", "SettingsLibraryDisplay.jsx"],
  reading: ["SettingsDialog.jsx"],
  translation: ["SettingsTranslation.jsx"],
  keyboard: ["SettingsKeyboard.jsx"],
  ai: ["SettingsDialog.jsx", "SettingsAi.jsx", "WebSearchSettings.jsx"],
  assistant: ["SettingsDialog.jsx", "AssistantTools.jsx"],
  "ai-advanced": ["SettingsDialog.jsx"],
  prompts: ["SettingsDialog.jsx"],
  integrations: ["SettingsIntegrations.jsx"],
  account: ["SettingsUsers.jsx", "SettingsSync.jsx", "SettingsMirrors.jsx", "SettingsCloudSignIn.jsx"],
  workspaces: ["SettingsWorkspace.jsx"],
  backups: ["SettingsBackups.jsx", "BackupTasks.jsx"],
  maintenance: ["SettingsDialog.jsx"],
  users: ["SettingsUsers.jsx"],
  server: ["SettingsServer.jsx", "SettingsPublicUrl.jsx", "SettingsGuests.jsx", "SettingsAi.jsx", "SettingsWorkspacesAdmin.jsx"],
  diagnostics: ["SettingsDialog.jsx"],
};
// Rows a pane draws from a table (label={label}): the table holds the t() call.
const FROM_TABLES = new Set(["Folder chat", "PDF chat", "Notes chat", "Single paper", "Metadata extraction", "Multi-paper total"]);

const esc = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// What becomes a data-setting: a Row's or Toggle's label, a Section's or
// PaneHead's title, an explicit data-setting.
function rendersSetting(files, target) {
  const key = `t\\("${esc(target)}"\\)`;
  const pattern = new RegExp(`<(?:Row|Toggle)\\b[^>]*?\\blabel=\\{${key}\\}|<(?:Section|PaneHead)\\b[^>]*?\\btitle=\\{${key}\\}|data-setting=\\{${key}\\}`);
  return files.some((file) => pattern.test(source(file))
    || (FROM_TABLES.has(target) && source(file).includes(`t("${target}")`)));
}

test("every search entry's jump target is a setting its pane renders", () => {
  for (const entry of SETTINGS_SEARCH) {
    const files = PANE_FILES[entry.pane];
    assert.ok(files, `${entry.label}: unknown pane "${entry.pane}"`);
    assert.ok(rendersSetting(files, entry.target), `${entry.pane} › ${entry.label}: no data-setting "${entry.target}" in ${files.join(", ")}`);
  }
});

test("every entry explains itself: a hint, and no label twice in one pane", () => {
  const seen = new Set();
  for (const entry of SETTINGS_SEARCH) {
    assert.ok(entry.hint, `${entry.label} has a hint`);
    const key = `${entry.pane}/${entry.label}`;
    assert.ok(!seen.has(key), `${key} is listed twice`);
    seen.add(key);
  }
});

test("labels that hold the query come first; hints and synonyms find the rest", () => {
  const all = Object.keys(PANE_FILES);
  const storage = searchSettings("storage", all).map((r) => r.label);
  assert.deepEqual(storage.slice(0, 1), ["Your storage"]);
  for (const label of ["Uploaded files", "Default quota", "Default max upload", "Save external PDFs"]) {
    assert.ok(storage.includes(label), `"storage" finds ${label}`);
  }
  assert.equal(searchSettings("enter key", all)[0].label, "Enter makes");
  assert.equal(searchSettings("delete", all)[0].target, "Personal", "a Manage-page setting jumps to the list it sits under");
  assert.ok(searchSettings("microsoft", all).some((r) => r.label === "Translation services"), "the hint is searched");
  for (const query of ["brave", "searxng", "search service", "preferred openai"]) {
    assert.ok(searchSettings(query, all).some((r) => r.pane === "ai" && r.target === "Web search"), query);
  }
});
