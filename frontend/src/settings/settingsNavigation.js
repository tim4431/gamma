import { t } from "../shared/i18n/i18n.js";
// Old entry points stay valid while everyday preferences use five destinations.
export const PANE_ALIASES = {
  general: "appearance", papers: "appearance", viewer: "reading", notes: "reading", search: "reading",
  context: "ai-advanced", library: "appearance", workspace: "workspaces", advanced: "diagnostics", sync: "account",
};
export const resolveSettingsPane = (pane) => PANE_ALIASES[pane] || pane;

// Search names the actual setting, including settings on the second-level AI pages.
const entries = [
  ["server", t("Public server URL"), "public address HTTPS remote proxy OAuth MCP assistant sign-in"],
  ["appearance", t("Theme"), "dark light gamma amber gold sepia solarized gray system colors"],
  ["appearance", t("Language"), "interface text locale english chinese 中文 语言"],
  ["appearance", t("Dark PDF pages"), "flip invert page colors"],
  ["appearance", t("Interface size"), "zoom text buttons controls scale touch"],
  ["appearance", t("Status bar"), "notifications messages"],
  ["appearance", t("Recents thumbnails"), "snapshots library home display cards"],
  ["appearance", t("File labels"), "folders chips library display cards"],
  ["appearance", t("Suggest tours"), "guide onboarding hints help"],
  ["reading", t("Imported annotations"), "hide strip keep remove originals highlights PDF"],
  ["reading", t("Open-access fallback"), "download PDF publisher free library"],
  ["reading", t("Auto-fetch metadata"), "title authors BibTeX DOI library"],
  ["reading", t("Save external PDFs"), "download storage offline URL library"],
  ["reading", t("Draws with"), "handwriting pen finger touch stylus ink"],
  ["reading", t("Stylus draws right away"), "handwriting pen ink"],
  ["reading", t("Pressure-sensitive strokes"), "handwriting pen ink width"],
  ["reading", t("Translation button"), "translation shortcut viewer"],
  ["reading", t("Translate into"), "translation language"],
  ["reading", t("Translate a selection"), "translation popup highlight selected text button"],
  ["reading", t("Translate on select"), "translation popup selected text automatic"],
  ["reading", t("Translate with"), "translation model engine service AI Google Youdao"],
  ["reading", t("Translation services"), "translation engine API key Google Cloud Youdao"],
  ["reading", t("Translation effort"), "translation reasoning thinking speed"],
  ["reading", t("Parallel requests"), "translation concurrency speed"],
  ["reading", t("Enter key"), "notes line keyboard shift"],
  ["reading", t("On the home page"), "search panel find bar expand results"],
  ["reading", t("On a page"), "search panel find bar expand results"],
  ["keyboard", t("Keyboard shortcuts"), "keys hotkeys bindings rebind command palette VSCode"],
  ["ai", t("Connections"), "provider credentials API key ChatGPT login service"],
  ["integrations", t("Integrations"), "external assistants Codex Claude Code plugins MCP tokens read-only revoke connections"],
  ["ai", t("Token usage"), "tokens statistics consumption cost input output cached reset"],
  ["ai", t("Default chat model"), "AI model"],
  ["ai", t("Metadata model"), "AI extraction identifiers"],
  ["ai", t("Dictation model"), "speech voice mic transcription"],
  ["ai", t("Dictation language"), "speech voice mic"],
  ["assistant", t("Assistant tools"), "allow master switch permissions agent"],
  ["assistant", t("Folder chat"), "permissions tools read search edit rename move"],
  ["assistant", t("PDF chat"), "permissions tools read search edit"],
  ["assistant", t("Notes chat"), "permissions tools read search edit"],
  ["assistant", t("Default reasoning effort"), "chat thinking effort"],
  ["assistant", t("Clear snapshots on click"), "chat images selections"],
  ["ai-advanced", t("Context size"), "context budget standard larger characters limits pages"],
  ["ai-advanced", t("Tool rounds"), "advanced tool requests limits"],
  ["ai-advanced", t("Read window"), "advanced context characters"],
  ["ai-advanced", t("Single paper"), "advanced context budget"],
  ["ai-advanced", t("Metadata extraction"), "advanced context budget"],
  ["ai-advanced", t("Multi-paper total"), "advanced context budget"],
  ["ai", t("Check at login"), "connection test credential ping"],
  ["prompts", t("Custom prompts"), "system prompt citation metadata agent"],
  ["account", t("Account"), "profile password storage quota sync Gamma Cloud"],
  ["workspaces", t("Workspaces"), "members sharing import export storage quota default"],
  ["account", t("Publishing"), "sync publish published pages Gamma Cloud share stop conflicts"],
  ["account", t("Clones"), "sync clone mirror offline copy remote workspace origin server pull push detach reattach conflicts"],
  ["account", t("Sync pill"), "sync status publish published pages Gamma Cloud header"],
  ["backups", t("Backups"), "snapshot restore merge download automatic scheduled tasks hourly daily weekly monthly cron retention"],
  ["maintenance", t("Library maintenance"), "metadata health text index rebuild papers"],
  ["users", t("Users"), "administration accounts password limits personal workspaces"],
  ["server", t("Server"), "administration storage defaults shared workspaces backups log"],
  ["server", t("Shared workspaces"), "administration new shared workspace members"],
  ["server", t("Shared AI provider"), "administration API key everyone lab members guests connection models"],
  ["server", t("Guest workspaces last"), "administration guests temporary expiry hours delete throwaway"],
  ["server", t("Demo mode"), "administration guests try the demo public login page first-run tour"],
  ["server", t("Allowance per account"), "administration shared AI tokens daily limit quota budget"],
  ["server", t("Allowance per guest"), "administration shared AI tokens daily limit quota budget guests"],
  ["ai", t("Shared allowance"), "shared AI tokens daily limit quota used up"],
  ["diagnostics", t("Debug logging"), "diagnostics tracing browser system log"],
];
export const SETTINGS_SEARCH = entries.map(([pane, label, keywords]) => ({ pane, label, keywords }));
export function searchSettings(query, allowedPanes) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return SETTINGS_SEARCH.filter(({ pane, label, keywords }) => allowedPanes.includes(pane)
    && words.every((word) => `${label} ${keywords}`.toLocaleLowerCase().includes(word)));
}
