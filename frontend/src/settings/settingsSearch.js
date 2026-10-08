// Settings search (docs/dev/settings.md): the catalog the dialog's search box
// reads, apart from settingsNavigation.js so App, which only resolves pane
// names, does not load it before the dialog opens.
import { t } from "../shared/i18n/i18n.js";

// Search names the actual setting, including settings on the second-level
// AI pages. Each entry: the pane, the setting's label (the `data-setting` the
// jump focuses — a Row's label, a Section's or PaneHead's title), the
// section it sits in and its one-line hint (the row's own words, same t()
// key), English synonyms, and — for a setting one level down, inside a
// workspace's Manage page or a row's menu — the `target` that is on the pane
// itself. tests/settingsSearch.test.mjs checks every target against its
// pane's source. An admin-only section of a pane everyone has (Backups ›
// Off-site copies) is wrapped in `admin()`.
const e = (pane, label, section, hint, keywords, target) => ({ pane, label, section, hint, keywords, target: target || label });
const admin = (entry) => ({ ...entry, admin: true });
const entries = [
  e("server", t("Public server URL"), t("Assistant connections"), t("The address assistants use to reach Gamma; HTTPS unless localhost"), "public address HTTPS remote proxy OAuth MCP assistant sign-in"),
  e("appearance", t("Theme"), null, t("System, light, dark and five more"), "dark light gamma amber gold sepia solarized gray system colors"),
  e("translation", t("Language"), t("Language"), t("Interface text only."), "interface text locale english chinese 中文 语言"),
  e("appearance", t("Dark PDF pages"), t("PDF pages"), t("Light text on a dark page; figures invert too."), "flip invert page colors"),
  e("appearance", t("Interface size"), t("Interface"), t("Text, buttons, icons and switches."), "zoom text buttons controls scale touch"),
  e("appearance", t("Status bar"), t("Interface"), t("Show the latest activity below your tabs."), "notifications messages"),
  e("appearance", t("Recents thumbnails"), t("Library"), t("Preview the page you last read."), "snapshots library home display cards"),
  e("appearance", t("File labels"), t("Library"), t("Show the folders a file belongs to."), "folders chips library display cards"),
  e("appearance", t("Sync pill"), t("Sync status"), t("Where the header shows a page's sync with Gamma Cloud."), "sync status publish published pages Gamma Cloud header"),
  e("appearance", t("Suggest tours"), t("Tours"), t("A short tour the first time a feature comes up."), "guide onboarding hints help"),
  e("reading", t("Imported annotations"), t("PDFs"), t("Annotations saved inside a PDF become highlights"), "hide strip keep remove originals highlights PDF"),
  e("reading", t("Open-access fallback"), t("PDFs"), t("Fetch a free copy when a publisher blocks the PDF"), "download PDF publisher free library"),
  e("reading", t("Auto-fetch metadata"), t("PDFs"), t("Title, authors and BibTeX on first open"), "title authors BibTeX DOI library"),
  e("reading", t("Save external PDFs"), t("PDFs"), t("Keep a server copy of PDFs opened from a URL"), "download storage offline URL library"),
  e("reading", t("Linked from"), t("Notes"), t("List the pages whose notes link to this one"), "backlinks linked mentions references links notes"),
  e("reading", t("Draws with"), t("Handwriting"), t("Pen only, or pen and finger"), "handwriting pen finger touch stylus ink"),
  e("reading", t("Stylus draws right away"), t("Handwriting"), t("Without opening the tools first"), "handwriting pen ink"),
  e("reading", t("Pressure-sensitive strokes"), t("Handwriting"), t("Pen strokes thicken with pressure"), "handwriting pen ink width"),
  e("reading", t("On the home page"), t("Search opens as"), t("Full panel: grouped result lists"), "search panel find bar expand results"),
  e("reading", t("On a page"), t("Search opens as"), t("Find bar: match counter and next / previous"), "search panel find bar expand results"),
  e("translation", t("Translation button"), t("Viewer & selection"), t("In the viewer; nothing translates until you ask"), "translation shortcut viewer"),
  e("translation", t("Translate into"), t("Viewer & selection"), t("The translated view's language"), "translation language"),
  e("translation", t("Translate a selection"), t("Viewer & selection"), t("A button next to the highlight colors"), "translation popup highlight selected text button"),
  e("translation", t("Translate on select"), t("Viewer & selection"), t("Without clicking the button first"), "translation popup selected text automatic"),
  e("translation", t("Translate with"), t("Service"), t("A chat model, or a translation service below"), "translation model engine service AI Google Youdao"),
  e("translation", t("Translation services"), t("Service"), t("Microsoft for free; Google or Youdao with a key"), "translation engine API key Google Cloud Youdao Microsoft"),
  e("translation", t("Translation effort"), t("Speed"), t("Low makes reasoning models translate much faster"), "translation reasoning thinking speed"),
  e("translation", t("Parallel requests"), t("Speed"), t("Translation calls in flight at once (1–4)"), "translation concurrency speed"),
  e("keyboard", t("Keyboard shortcuts"), null, t("Every command's keys, rebindable"), "keys hotkeys bindings rebind command palette VSCode"),
  e("keyboard", t("Enter makes"), t("Built in"), t("A new note or a new line; Shift+Enter makes the other"), "enter key new note line break notes shift return"),
  e("ai", t("Connections"), null, t("AI providers, keys and ChatGPT sign-in"), "provider credentials API key ChatGPT login service"),
  e("ai", t("Check at login"), t("Connection check"), t("Verify the active provider when Gamma opens"), "connection test credential ping"),
  e("ai", t("Default chat model"), t("Models"), t("Also used by citations and generated titles"), "AI model"),
  e("ai", t("Metadata model"), t("Models"), t("Used only when identifiers cannot resolve the paper"), "AI extraction identifiers"),
  e("ai", t("Dictation model"), t("Models"), t("For the chat mic button; needs an OpenAI key"), "speech voice mic transcription"),
  e("ai", t("Dictation language"), t("Models"), t("Naming the language improves accuracy"), "speech voice mic"),
  e("ai", t("Token usage"), null, t("Tokens in and out, per model"), "tokens statistics consumption cost input output cached reset"),
  e("ai", t("Shared allowance"), t("Token usage"), t("What the server's shared keys still allow you today"), "shared AI tokens daily limit quota used up"),
  e("assistant", t("Default reasoning effort"), t("Chat"), t("Each model gets the nearest level it takes"), "chat thinking effort reasoning level"),
  e("assistant", t("Default speed"), t("Chat"), t("Only models whose provider offers that tier"), "chat fast mode speed service tier priority flex latency"),
  e("assistant", t("Clear snapshots on click"), t("Chat"), t("A plain click in the PDF also drops pending snapshots"), "chat images selections"),
  e("assistant", t("Prompts"), null, t("Chat, metadata, citation and agent prompts"), "custom system prompt citation metadata agent"),
  e("assistant", t("Context size"), null, t("How much paper text a chat reads"), "context budget standard larger characters limits pages"),
  e("assistant", t("Single paper"), t("Context size"), t("Read from the open paper for one chat message"), "advanced context budget"),
  e("assistant", t("Metadata extraction"), t("Context size"), t("Read while detecting identifiers and extracting fields"), "advanced context budget"),
  e("assistant", t("Multi-paper total"), t("Context size"), t("Shared evenly by every selected paper"), "advanced context budget"),
  e("tools", t("Tool usage"), null, t("What chats may do, where they search and how far they go"), "agent tools permissions access control online search limits"),
  e("tools", t("Assistant tools"), t("Tools"), t("Let chats read, search and edit your library"), "allow master switch permissions agent access"),
  e("tools", t("Search papers online"), t("Tools"), t("Find papers on Crossref, arXiv and OpenAlex, follow their citations, and search the web"), "web internet research references citations", t("Tools")),
  e("tools", t("Fetch documents"), t("Tools"), t("Read a DOI or URL without saving it to your library"), "download web internet PDF", t("Tools")),
  e("tools", t("Use journal sign-ins"), t("Tools"), t("Use connected publisher cookies when fetching documents"), "cookies publisher journals session access", t("Tools")),
  e("tools", t("Folder chat"), t("Tools"), t("Home and folder views"), "permissions tools read search edit rename move access"),
  e("tools", t("PDF chat"), t("Tools"), t("Pages with a PDF"), "permissions tools read search edit access"),
  e("tools", t("Notes chat"), t("Tools"), t("Note pages"), "permissions tools read search edit access"),
  e("tools", t("Fetch blocked papers in the background"), t("Tools"), t("Gamma Connector tries in an unfocused tab and sends the PDF back"), "connector extension paywall sign-in captcha download"),
  e("tools", t("Read long papers with a helper"), t("Tools"), t("A second pass reads the document and hands back a cited answer"), "delegate subagent read_paper long document tokens"),
  e("tools", t("Search the web with"), t("Online search"), t("Lab pages, repositories and copies the registries miss"), "web internet search engine Brave SearXNG ChatGPT OpenAI Anthropic Google"),
  e("tools", t("Online search services"), t("Online search"), t("Brave Search, SearXNG and an OpenAlex key"), "API key Brave SearXNG OpenAlex budget"),
  e("tools", t("Tool rounds"), t("Tool limits"), t("AI ↔ tool round-trips per message"), "advanced tool requests limits"),
  e("tools", t("Read window"), t("Tool limits"), t("Document text per read tool call"), "advanced context characters"),
  e("integrations", t("Integrations"), null, t("Read-only access for Codex, Claude Code, DeepSeek Harness, or any MCP assistant."), "external assistants Codex Claude Code plugins MCP tokens read-only revoke connections"),
  e("account", t("Account & sync"), null, t("Your account, Gamma Cloud link and settings sync"), "profile account user"),
  e("account", t("Gamma Cloud"), null, t("Publish pages for free and carry your settings to your other servers"), "cloud link unlink sign-in identity plan publish sync set up"),
  e("account", t("Settings sync"), null, t("Carry these settings to your other Gamma servers"), "sync cloud fetch push profile preferences"),
  e("account", t("Publishing"), null, t("Pages you publish to Gamma Cloud"), "sync publish published pages Gamma Cloud share stop conflicts"),
  e("workspaces", t("Workspaces"), null, t("Personal and shared workspaces, export and import"), "library libraries default new personal shared"),
  e("workspaces", t("Clones"), null, t("Offline copies of workspaces on other Gamma servers"), "sync clone mirror offline copy remote workspace origin server pull push detach reattach conflicts"),
  e("workspaces", t("Your storage"), t("Storage"), t("all personal workspaces"), "storage quota space used disk"),
  e("workspaces", t("Members"), t("Manage"), t("Invite people and set roles, in a shared workspace's Manage page"), "invite members roles sharing owner editor viewer people", t("Shared")),
  e("workspaces", t("Export workspace"), t("Data"), t("The Data menu on a workspace's row; Export all for every one"), "export download zip data import merge", t("Personal")),
  e("workspaces", t("Delete workspace"), t("Manage"), t("In a workspace's Manage page, under Danger zone"), "delete remove leave destroy", t("Personal")),
  e("backups", t("Backups"), null, t("Server-kept snapshots and the tasks that take them."), "snapshot backup"),
  e("backups", t("Periodic backup tasks"), null, t("Hourly, daily, weekly or monthly snapshots"), "automatic scheduled tasks hourly daily weekly monthly cron retention schedule"),
  e("backups", t("Restore a backup"), t("Saved snapshots"), t("A snapshot's Restore menu: replace or merge"), "restore replace merge snapshot roll back download", t("Saved snapshots")),
  admin(e("backups", t("Off-site copies"), null, t("Databases and uploaded files, every so often; Gamma keeps running on its own disk"), "administration offsite backup bucket S3 R2 Cloudflare AWS MinIO cloud remote storage replicate disaster recovery")),
  e("maintenance", t("Library maintenance"), null, t("Storage, search index and metadata health"), "metadata health text index rebuild papers"),
  e("maintenance", t("Uploaded files"), t("Storage"), t("PDFs and images on the server"), "storage quota uploads space used disk"),
  e("users", t("Users"), null, t("Accounts, passwords and storage limits"), "administration accounts limits personal workspaces"),
  e("users", t("Change password"), t("Edit"), t("Edit on an account's row sets a new password"), "administration password credentials reset account", t("Users")),
  e("server", t("Server"), null, t("Dashboard, updates, sign-in, guests and the log"), "administration dashboard version update log"),
  e("server", t("Default quota"), t("Storage defaults"), t("Personal uploads per account; 0 = unlimited"), "administration storage quota limit space"),
  e("server", t("Default max upload"), t("Storage defaults"), t("Largest single file; Users can override per account"), "administration storage upload size limit file"),
  e("server", t("Shared workspaces"), null, t("Workspaces an admin makes for a team"), "administration new shared workspace members"),
  e("server", t("Shared AI provider"), null, t("AI keys every account on this server can use"), "administration API key everyone lab members guests connection models"),
  e("server", t("Allowance per account"), t("Shared AI provider"), t("Tokens a day on the shared keys; 0 = unlimited"), "administration shared AI tokens daily limit quota budget"),
  e("server", t("Allowance per guest"), t("Shared AI provider"), t("Tokens a day for each guest; 0 = unlimited"), "administration shared AI tokens daily limit quota budget guests"),
  e("server", t("Guest sign-in"), t("Guests"), t("The login page offers a throwaway account"), "administration guests visitors anonymous turn off disable try it login button"),
  e("server", t("Guest workspaces last"), t("Guests"), t("Then the guest's account and workspace are deleted"), "administration guests temporary expiry hours delete throwaway"),
  e("server", t("Demo mode"), t("Guests"), t("The login page leads with Try the demo"), "administration guests try the demo public login page first-run tour"),
  e("server", t("Check databases"), t("Databases"), t("a quick check of every account and workspace database"), "administration integrity corruption damaged sqlite quick_check health"),
  e("diagnostics", t("Debug logging"), t("Tracing"), t("Trace reading-position, restore and sync events"), "diagnostics tracing browser system log"),
];
export const SETTINGS_SEARCH = entries;

// Every word of the query must appear in the entry's label, synonyms, hint
// or section; entries whose label holds every word come first, then the
// rest, each group in table order. An `admin` entry is offered only while
// the Server pane is, which the dialog shows to admins alone.
export function searchSettings(query, allowedPanes) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const has = (text) => words.every((word) => text.toLocaleLowerCase().includes(word));
  const isAdmin = allowedPanes.includes("server");
  const found = entries.filter((entry) => allowedPanes.includes(entry.pane) && (!entry.admin || isAdmin)
    && has(`${entry.label} ${entry.keywords} ${entry.hint || ""} ${entry.section || ""}`));
  return [...found.filter((entry) => has(entry.label)), ...found.filter((entry) => !has(entry.label))];
}
