import test from "node:test";
import assert from "node:assert/strict";
import { permissionPreset, presetPermissions, toolsForKind } from "../src/chat/chatSettings.js";
import { resolveSettingsPane, searchSettings } from "../src/settings/settingsNavigation.js";

test("permission presets preserve explicit restrictions and the applicable tools for each chat kind", () => {
  for (const kind of ["folder", "pdf", "notes"]) {
    const read = presetPermissions(kind, "read");
    assert.equal(permissionPreset(kind, read), "read");
    assert.equal(read.block_edit, false);
    assert.equal(read.read, true);
    assert.deepEqual(Object.keys(read), toolsForKind(kind));
    assert.equal(permissionPreset(kind, { ...read, read: false }), "custom");
    assert.equal(permissionPreset(kind, presetPermissions(kind, "edit")), "edit");
    assert.equal(permissionPreset(kind, {}), "edit", "legacy omitted permissions remain allowed");
  }
  assert.equal(presetPermissions("folder", "read").rename, false);
  assert.equal(presetPermissions("pdf", "edit").rename, undefined);
});

test("settings search finds controls on nested AI pages without exposing inaccessible management pages", () => {
  const allowed = ["appearance", "reading", "ai", "assistant", "ai-advanced", "prompts", "account", "maintenance", "diagnostics"];
  assert.equal(searchSettings("translation concurrency", allowed)[0].label, "Parallel requests");
  assert.equal(searchSettings("  FLIP colors  ", allowed)[0].pane, "appearance");
  assert.equal(searchSettings("password", allowed).some((item) => item.pane === "users"), false);
  assert.equal(searchSettings("password", [...allowed, "users"]).some((item) => item.pane === "users"), true);
  assert.deepEqual(searchSettings("   ", allowed), []);
  assert.deepEqual(searchSettings("no-such-setting", allowed), []);
});

test("legacy settings destinations resolve to the reorganized pages", () => {
  for (const old of ["notes", "viewer", "search"]) assert.equal(resolveSettingsPane(old), "reading");
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
    agentPerms: { pdf: { block_edit: false } },
  });
  assert.equal(read.theme, "sepia");
  assert.equal(read.toolRounds, 12);
  assert.equal(read.chatSystem, "Be brief.");
  for (const dropped of ["pdfDarkPage", "translateLang", "translateParallel", "uiScale", "inkTools", "unknownPref"]) {
    assert.ok(!(dropped in read), `${dropped} dropped`);
  }
  assert.equal(read.agentPerms.pdf.block_edit, false);
  assert.equal(read.agentPerms.pdf.read, true, "missing tools stay allowed");
  assert.equal(read.agentPerms.folder.rename, true);
  for (const bad of [null, "x", [], 3]) assert.deepEqual(readProfile(bad), {});
  const bytes = JSON.stringify({ value: profileOf({ ...defaults, chatSystem: "p".repeat(12000), agentSystem: "p".repeat(12000) }) }).length;
  assert.ok(bytes < 64 * 1024, "fits the prefs size cap");
});
