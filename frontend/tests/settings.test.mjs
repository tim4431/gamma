import test from "node:test";
import assert from "node:assert/strict";
import { normalizePerm, permStates, permissionPreset, presetPermissions, toolsForKind } from "../src/chat/chatSettings.js";
import { resolveSettingsPane, searchSettings } from "../src/settings/settingsNavigation.js";

test("permission presets cover each chat kind's tools, and changes ask unless told otherwise", () => {
  for (const kind of ["folder", "pdf", "notes"]) {
    const read = presetPermissions(kind, "read");
    assert.equal(permissionPreset(kind, read), "read");
    assert.equal(read.block_edit, "off");
    assert.equal(read.read, "allow");
    assert.equal(read.publisher_cookies, "allow");
    assert.equal(permissionPreset(kind, { ...read, publisher_cookies: "off" }), "custom");
    const library = presetPermissions(kind, "library");
    assert.equal(permissionPreset(kind, library), "library");
    assert.equal(library.read, "allow");
    for (const key of ["web_search", "web_read", "publisher_cookies", "block_edit"]) {
      assert.equal(library[key], "off", `read-library turns ${key} off`);
    }
    assert.deepEqual(Object.keys(read), toolsForKind(kind));
    assert.equal(permissionPreset(kind, { ...read, read: "off" }), "custom");
    const ask = presetPermissions(kind, "ask");
    assert.equal(ask.block_edit, "ask");
    assert.equal(ask.web_read, "allow");
    assert.equal(permissionPreset(kind, ask), "ask");
    assert.equal(permissionPreset(kind, presetPermissions(kind, "all")), "all");
    assert.equal(permissionPreset(kind, {}), "ask", "left out, reading is allowed and changes ask");
  }
  assert.equal(presetPermissions("folder", "read").rename, "off");
  assert.equal(presetPermissions("pdf", "all").rename, undefined);
});

test("a stored permission reads as a state", () => {
  assert.equal(normalizePerm("rename", true), "ask", "a change that was on asks now");
  assert.equal(normalizePerm("read", true), "allow");
  assert.equal(normalizePerm("read", false), "off");
  assert.equal(normalizePerm("block_edit", "allow"), "allow");
  assert.equal(normalizePerm("block_edit", undefined), "ask");
  assert.equal(normalizePerm("web_read", "bogus"), "allow");
  assert.equal(normalizePerm("publisher_cookies", "ask"), "allow", "journal sign-ins are on or off");
  assert.deepEqual(permStates("publisher_cookies"), ["allow", "off"]);
  assert.deepEqual(permStates("rename"), ["allow", "ask", "off"]);
});

test("settings search finds controls on nested AI pages without exposing inaccessible management pages", () => {
  const allowed = ["appearance", "reading", "translation", "ai", "assistant", "ai-advanced", "prompts", "account", "maintenance", "diagnostics"];
  assert.equal(searchSettings("translation concurrency", allowed)[0].label, "Parallel requests");
  assert.equal(searchSettings("  FLIP colors  ", allowed)[0].pane, "appearance");
  assert.equal(searchSettings("password", allowed).some((item) => item.pane === "users"), false);
  assert.equal(searchSettings("password", [...allowed, "users"]).some((item) => item.pane === "users"), true);
  assert.deepEqual(searchSettings("   ", allowed), []);
  assert.deepEqual(searchSettings("no-such-setting", allowed), []);
});

test("legacy settings destinations resolve to the reorganized pages", () => {
  for (const old of ["viewer", "search"]) assert.equal(resolveSettingsPane(old), "reading");
  assert.equal(resolveSettingsPane("notes"), "keyboard");
  assert.equal(resolveSettingsPane("context"), "ai-advanced");
  assert.equal(resolveSettingsPane("library"), "appearance");
  for (const id of ["assistant", "prompts", "ai-advanced"]) assert.equal(resolveSettingsPane(id), id);
  assert.equal(resolveSettingsPane("general"), "appearance");
  assert.equal(resolveSettingsPane("workspace"), "workspaces");
  assert.equal(resolveSettingsPane("advanced"), "diagnostics");
  assert.equal(resolveSettingsPane("account"), "account");
});

test("every preference declares one scope; the profile carries exactly the account-scoped ones", async () => {
  const { PREFS, ACCOUNT, BROWSER, ACCOUNT_PREFS } = await import("../src/app/prefDefs.js");
  const keys = new Set();
  for (const [name, def] of Object.entries(PREFS)) {
    assert.ok([ACCOUNT, BROWSER].includes(def.scope), `${name} has a scope`);
    assert.ok(def.key.startsWith("gamma-") && !keys.has(def.key), `${name} has its own storage key`);
    keys.add(def.key);
  }
  const account = new Set(ACCOUNT_PREFS);
  // Appearance, reading, library, chat behaviour, budgets and prompts follow the account…
  for (const name of ["theme", "pdfDarkPage", "embAnnots", "translateLang", "enterNewNote", "searchDetailsHome",
    "fileLabels", "recentThumbs", "agentEnabled", "agentPerms", "chatEffort", "chatContextChars", "toolRounds", "chatSystem"]) {
    assert.ok(account.has(name), `${name} is account-scoped`);
  }
  // …what describes this device, or names this server's provider entries, stays here.
  for (const name of ["uiScale", "statusBarVisible", "inkPenOnly", "inkTools", "inkEraserSize", "metaModel", "translateModel"]) {
    assert.ok(!account.has(name), `${name} is browser-scoped`);
  }
});

test("the profile codec keeps valid entries and drops the rest", async () => {
  const { PREFS, ACCOUNT_PREFS, profileOf, readProfile, setterName } = await import("../src/app/prefDefs.js");
  const defaults = Object.fromEntries(Object.entries(PREFS).map(([name, def]) => [name, def.default]));
  const profile = profileOf(defaults);
  assert.deepEqual(Object.keys(profile), ACCOUNT_PREFS);
  assert.deepEqual(readProfile(JSON.parse(JSON.stringify(profile))), profile, "defaults round-trip");
  assert.equal(setterName("pdfDarkPage"), "setPdfDarkPage");
  const read = readProfile({
    theme: "sepia", pdfDarkPage: "yes", translateLang: "xx", translateParallel: 99, toolRounds: 12,
    chatSystem: "Be brief.", uiScale: 1.4, inkTools: [], unknownPref: 1,
    agentPerms: { pdf: { block_edit: false, publisher_cookies: false } },
  });
  assert.equal(read.theme, "sepia");
  assert.equal(read.toolRounds, 12);
  assert.equal(read.chatSystem, "Be brief.");
  assert.equal(read.translateParallel, 4, "a count stored under the old 1–32 range is clamped, not dropped");
  for (const dropped of ["pdfDarkPage", "translateLang", "uiScale", "inkTools", "unknownPref"]) {
    assert.ok(!(dropped in read), `${dropped} dropped`);
  }
  assert.equal(read.agentPerms.pdf.block_edit, "off");
  assert.equal(read.agentPerms.pdf.publisher_cookies, "off");
  assert.equal(read.agentPerms.folder.publisher_cookies, "allow", "existing accounts keep connected fetching enabled");
  assert.equal(read.agentPerms.pdf.read, "allow", "missing reading tools are allowed");
  assert.equal(read.agentPerms.folder.rename, "ask", "a missing change asks");
  const legacy = readProfile({ agentPerms: { folder: { rename: true, read: true, move: "allow" } } });
  assert.deepEqual([legacy.agentPerms.folder.rename, legacy.agentPerms.folder.read, legacy.agentPerms.folder.move],
    ["ask", "allow", "allow"], "a change stored as on from before Ask asks; an explicit Allow stays");
  for (const bad of [null, "x", [], 3]) assert.deepEqual(readProfile(bad), {});
  const bytes = JSON.stringify({ value: profileOf({ ...defaults, chatSystem: "p".repeat(12000), agentSystem: "p".repeat(12000) }) }).length;
  assert.ok(bytes < 64 * 1024, "fits the prefs size cap");
});

test("the translated view's language defaults to the browser's when it is on offer", async () => {
  const { defaultTranslateLang } = await import("../src/app/prefDefs.js");
  assert.equal(defaultTranslateLang(["en-GB", "zh-CN"]), "en");
  assert.equal(defaultTranslateLang(["de-AT"]), "de");
  assert.equal(defaultTranslateLang(["zh"]), "zh-CN");
  assert.equal(defaultTranslateLang(["zh-TW"]), "zh-TW");
  assert.equal(defaultTranslateLang(["zh-Hant-HK"]), "zh-TW");
  assert.equal(defaultTranslateLang(["nl-NL", "ja-JP"]), "ja", "the first language on offer");
  assert.equal(defaultTranslateLang(["nl"]), "zh-CN");
  assert.equal(defaultTranslateLang([]), "zh-CN");
});

test("the translation pick resolves to what is sent: the pick, the free service, or the chat model", async () => {
  const { FREE_TRANSLATE_ENGINE, translateModelFor } = await import("../src/app/prefDefs.js");
  const free = { id: FREE_TRANSLATE_ENGINE }, google = { id: "engine:google" }, model = { id: "p1:gpt" };
  assert.equal(translateModelFor("engine:google", [free, google], [model]), "engine:google");
  assert.equal(translateModelFor("p1:gpt", [free], [model]), "p1:gpt");
  // A stale pick (the service removed) follows the chat model while there is one...
  assert.equal(translateModelFor("engine:youdao", [free], [model]), "");
  assert.equal(translateModelFor("", [free], [model]), "");
  // ...and falls to the free service when there is no chat model at all.
  assert.equal(translateModelFor("engine:youdao", [free], []), FREE_TRANSLATE_ENGINE);
  assert.equal(translateModelFor("", [], []), "");
});
