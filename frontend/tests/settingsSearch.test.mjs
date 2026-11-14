// Settings search (settings/settingsSearch.js): every entry must jump to
// something its pane really renders, or the jump finds nothing.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SETTINGS_SEARCH, hostCaps, searchSettings } from "../src/settings/settingsSearch.js";
import { TOPBAR_ITEMS } from "../src/app/topbarItems.js";
import { VIEWER_TOOL_ITEMS } from "../src/app/viewerToolItems.js";

const SETTINGS = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/settings");
const source = (file) => fs.readFileSync(path.join(SETTINGS, file), "utf8");

// The sources each pane renders: its own file and the sections it pulls in.
const PANE_FILES = {
  appearance: ["SettingsAppearance.jsx", "SettingsLibraryDisplay.jsx", "TextHighlighters.jsx", "BarLayoutSection.jsx", "../app/topbarItems.js", "../app/viewerToolItems.js"],
  reading: ["SettingsDialog.jsx"],
  translation: ["SettingsTranslation.jsx"],
  keyboard: ["SettingsKeyboard.jsx"],
  ai: ["SettingsDialog.jsx", "SettingsAi.jsx"],
  assistant: ["SettingsDialog.jsx"],
  tools: ["SettingsDialog.jsx", "AssistantTools.jsx", "OnlineSearch.jsx"],
  integrations: ["SettingsIntegrations.jsx"],
  account: ["SettingsUsers.jsx", "SettingsSync.jsx", "SettingsMirrors.jsx", "SettingsCloudSignIn.jsx", "SettingsZotero.jsx"],
  workspaces: ["SettingsWorkspace.jsx", "SettingsMirrors.jsx"],
  backups: ["SettingsBackups.jsx", "BackupTasks.jsx", "OffsiteCopies.jsx"],
  maintenance: ["SettingsDialog.jsx"],
  users: ["SettingsUsers.jsx"],
  server: ["SettingsServer.jsx", "SettingsPublicUrl.jsx", "SettingsGuests.jsx", "SettingsAi.jsx", "SettingsWorkspacesAdmin.jsx"],
  proxy: ["SettingsProxy.jsx"],
  diagnostics: ["SettingsDialog.jsx"],
};
// Rows a pane draws from a table (label={label}): the table holds the t()
// call, or the T() of a registry the row translates (the Top bar's and the
// Viewer tools' items; the pen section's words, SettingsDialog.jsx PEN_WORDS).
const FROM_TABLES = new Set(["Folder chat", "PDF chat", "Notes chat", "Single paper", "Metadata extraction", "Multi-paper total",
  "Double tap", "Squeeze", "Pen button", "Second pen button",
  ...TOPBAR_ITEMS.map((item) => item.label), ...VIEWER_TOOL_ITEMS.map((item) => item.label)]);
// The hosts the pen section is worded for: the iPad app's HostInfo
// (ipad/GammaIPad/Shell/Bridge.swift) and the Android app's.
const IPAD = { platform: "ipad", version: "0.2.0", ink: true, hold: true, pencil: true, pencilPhases: true, pencilRoll: true, haptics: true };
const ANDROID = { platform: "android", version: "0.1.0", ink: true, hold: true, pencil: true, pencilGestures: ["tap", "squeeze"], haptics: true, save: true };

const esc = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// What becomes a data-setting: a Row's or Toggle's label, a Section's or
// PaneHead's title, an explicit data-setting.
function rendersSetting(files, target) {
  const key = `t\\("${esc(target)}"\\)`;
  const pattern = new RegExp(`<(?:Row|Toggle)\\b[^>]*?\\blabel=\\{${key}\\}|<(?:Section|BarLayoutSection|PaneHead)\\b[^>]*?\\btitle=\\{${key}\\}|data-setting=\\{${key}\\}`);
  return files.some((file) => pattern.test(source(file))
    || (FROM_TABLES.has(target) && (source(file).includes(`t("${target}")`) || source(file).includes(`T("${target}")`))));
}

test("every search entry's jump target is a setting its pane renders", () => {
  for (const entry of SETTINGS_SEARCH) {
    const files = PANE_FILES[entry.pane];
    assert.ok(files, `${entry.label}: unknown pane "${entry.pane}"`);
    assert.ok(rendersSetting(files, entry.target), `${entry.pane} › ${entry.label}: no data-setting "${entry.target}" in ${files.join(", ")}`);
  }
});

test("every entry explains itself: a hint, and no label twice in one pane under any one host", () => {
  for (const entry of SETTINGS_SEARCH) assert.ok(entry.hint, `${entry.label} has a hint`);
  for (const host of [null, hostCaps(IPAD), hostCaps(ANDROID)]) {
    const seen = new Set();
    for (const entry of SETTINGS_SEARCH.filter((e) => !e.host || e.host.every((flag) => !!host?.[flag]))) {
      const key = `${entry.pane}/${entry.label}`;
      assert.ok(!seen.has(key), `${key} is listed twice under ${host?.platform || "no host"}`);
      seen.add(key);
    }
  }
});

test("labels that hold the query come first; hints and synonyms find the rest", () => {
  const all = Object.keys(PANE_FILES);
  const storage = searchSettings("storage", all).map((r) => r.label);
  assert.deepEqual(storage.slice(0, 1), ["Your storage"]);
  for (const label of ["Uploaded files", "Default quota", "Save external PDFs"]) {
    assert.ok(storage.includes(label), `"storage" finds ${label}`);
  }
  assert.equal(searchSettings("enter key", all)[0].label, "Enter makes");
  assert.equal(searchSettings("delete", all)[0].target, "Personal", "a Manage-page setting jumps to the list it sits under");
  assert.ok(searchSettings("microsoft", all).some((r) => r.label === "Translation services"), "the hint is searched");
  // the Zotero account's row, and the section that lists the clones and the links
  const zotero = searchSettings("zotero", all).map((r) => `${r.pane}/${r.label}`);
  assert.deepEqual(zotero.slice(0, 1), ["account/Zotero"]);
  assert.ok(zotero.includes("workspaces/Clones and Zotero links"), zotero.join(", "));
});

test("an admin-only section is found only by admins (the Server pane is theirs alone)", () => {
  const all = Object.keys(PANE_FILES);
  assert.equal(searchSettings("bucket", all)[0]?.label, "Off-site copies");
  const member = all.filter((pane) => !["server", "users", "proxy"].includes(pane));
  assert.ok(!searchSettings("bucket", member).some((r) => r.label === "Off-site copies"));
});

test("Network proxy is found where its pane is offered (admins), and a row shown only under Manual lands on the Proxy row", () => {
  const all = Object.keys(PANE_FILES);
  const found = searchSettings("socks5", all);
  assert.equal(found[0]?.label, "Network proxy");
  assert.equal(found.find((r) => r.label === "Proxy server")?.target, "Proxy");
  assert.deepEqual(searchSettings("socks5", all.filter((pane) => pane !== "proxy")), [], "no pane, no match");
});

test("an Apple Pencil setting is found only under a native host with its flag (the iPad app)", () => {
  const all = Object.keys(PANE_FILES);
  const labels = (query, opts) => searchSettings(query, all, opts).map((r) => r.label);
  const ipad = (flags) => ({ host: hostCaps({ platform: "ipad", ...flags }) });
  for (const opts of [undefined, {}, { host: null }, ipad({ ink: true })]) {
    assert.deepEqual(labels("squeeze", opts), [], `no host, no Pencil: ${JSON.stringify(opts)}`);
    assert.ok(!labels("haptics", opts).includes("Haptics"));
  }
  assert.deepEqual(labels("squeeze", ipad({ pencil: true })), ["Squeeze"]);
  assert.ok(labels("apple pencil", ipad({ pencil: true })).includes("Double tap"));
  assert.ok(!labels("haptic", ipad({ pencil: true })).includes("Haptics"), "haptics need a host that plays them");
  assert.ok(!labels("haptic", ipad({ haptics: true })).includes("Haptics"), "and its row is in the Apple Pencil section, shown under pencil");
  assert.ok(labels("haptic", ipad({ pencil: true, haptics: true })).includes("Haptics"));
  assert.ok(!labels("pen button", ipad({ pencil: true })).some((l) => /pen button/i.test(l)), "the iPad's rows keep Apple's names");
  // an iPad build that names its gestures: a row for each one it forwards
  assert.deepEqual(labels("squeeze", ipad({ pencil: true, pencilGestures: ["tap"] })), []);
  assert.ok(labels("double tap", ipad({ pencil: true, pencilGestures: ["tap"] })).includes("Double tap"));
  assert.ok(labels("hover", {}).includes("Hover preview"), "a browser's own row is everyone's");
});

test("under any other host the pen section is a stylus's: its buttons, no Apple names, each row where the host forwards its gesture", () => {
  const all = Object.keys(PANE_FILES);
  // the host-only entries a query finds (other panes have buttons too)
  const found = (query, host) => searchSettings(query, all, { host: hostCaps(host) }).filter((r) => r.host).map((r) => `${r.section} › ${r.label}`);
  assert.deepEqual(found("button", ANDROID), ["Stylus › Pen button", "Stylus › Second pen button"]);
  assert.deepEqual(found("haptic", ANDROID), ["Stylus › Haptics"]);
  assert.ok(found("stylus", ANDROID).includes("Stylus › Pen button"));
  for (const query of ["squeeze", "apple", "pencil pro", "ipad", "double tap"]) assert.deepEqual(found(query, ANDROID), [], query);
  assert.deepEqual(found("button", { ...ANDROID, pencilGestures: ["tap"] }), ["Stylus › Pen button"], "a host that forwards one gesture");
  assert.deepEqual(found("button", { ...ANDROID, pencilGestures: [] }), []);
  assert.deepEqual(found("haptic", { ...ANDROID, haptics: false }), [], "no vibrator, no Haptics");
  assert.deepEqual(found("button", { ...ANDROID, pencil: false }), [], "no gestures forwarded, no section");
});

test("Delete Gamma Cloud account is the apps' alone, under any native host; the privacy policy is everyone's", () => {
  const all = Object.keys(PANE_FILES);
  const found = (query, host) => searchSettings(query, all, { host: hostCaps(host) }).map((r) => `${r.pane}/${r.label}`);
  for (const host of [IPAD, ANDROID, { platform: "android" }]) {
    assert.ok(found("delete account", host).includes("account/Delete Gamma Cloud account"), host.platform);
    assert.ok(found("cloud", host).includes("account/Delete Gamma Cloud account"), host.platform);
  }
  assert.ok(!found("delete account", null).includes("account/Delete Gamma Cloud account"), "a browser has Open account, not the row");
  for (const host of [null, IPAD, ANDROID]) {
    assert.deepEqual(found("privacy", host).slice(0, 1), ["diagnostics/Privacy policy"], host?.platform || "no host");
  }
});

test("Add opened PDFs to the library is found in the desktop app only, which alone reads it", () => {
  const all = Object.keys(PANE_FILES);
  const found = (opts) => searchSettings("opened PDFs library", all, opts).map((r) => r.label);
  assert.ok(found({ desktop: true }).includes("Add opened PDFs to the library"));
  for (const opts of [{}, { host: hostCaps(IPAD) }, { host: hostCaps(ANDROID) }]) {
    assert.ok(!found(opts).includes("Add opened PDFs to the library"), opts.host?.platform || "a browser");
  }
  assert.match(source("SettingsDialog.jsx"), /\{IS_DESKTOP \? \(\s*<Toggle\s+icon=\{FolderPlusIcon\}\s+label=\{t\("Add opened PDFs to the library"\)\}/,
    "and its row is drawn there only");
});

test("hostCaps: the platform's words and the gestures a host forwards", () => {
  assert.equal(hostCaps(null), null);
  const ipad = hostCaps(IPAD);
  assert.deepEqual([ipad.apple, ipad.stylus, ipad.pencilTap, ipad.pencilSqueeze], [true, false, true, true], "an iPad without pencilGestures forwards both");
  assert.equal(ipad.pencilPhases, true, "the host's own flags come along");
  const android = hostCaps({ ...ANDROID, pencilGestures: ["squeeze"] });
  assert.deepEqual([android.apple, android.stylus, android.pencilTap, android.pencilSqueeze], [false, true, false, true]);
});
