import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ACCOUNT_PREFS } from "../src/app/prefDefs.js";
import { SECTION_PREFS, UNTAGGED_PREFS, accountPrefs } from "../src/settings/sectionPrefs.js";

const SETTINGS = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/settings");

// Every `scope="account"` in the panes, with the Section title before it and
// the table entry its `prefs` prop names.
function accountSections() {
  const found = [];
  for (const file of fs.readdirSync(SETTINGS).filter((f) => f.endsWith(".jsx"))) {
    const source = fs.readFileSync(path.join(SETTINGS, file), "utf8");
    for (const match of source.matchAll(/scope="account"/g)) {
      const open = source.lastIndexOf("<Section", match.index);
      const title = /title="([^"]+)"/.exec(source.slice(open, match.index))?.[1];
      const ref = /^scope="account"\s+prefs=\{SECTION_PREFS\.(\w+)\["([^"]+)"\]\}/.exec(source.slice(match.index));
      found.push({ file, title, pane: ref?.[1], entry: ref?.[2] });
    }
  }
  return found;
}

test("the helper accepts account preferences and throws on anything else", () => {
  assert.deepEqual([...accountPrefs(["theme", "enterNewNote"])], ["theme", "enterNewNote"]);
  assert.throws(() => accountPrefs(["noSuchPref"]), /unknown preference "noSuchPref"/);
  assert.throws(() => accountPrefs(["uiScale"]), /"uiScale" is not an account preference/);
});

test("every table entry names account preferences", () => {
  for (const [pane, sections] of Object.entries(SECTION_PREFS)) {
    for (const [title, names] of Object.entries(sections)) {
      assert.ok(names.length, `${pane} › ${title} holds no preferences`);
      assert.doesNotThrow(() => accountPrefs(names), `${pane} › ${title}`);
    }
  }
});

test("every account section in the panes passes its own entry as prefs, and every entry is used once", () => {
  const sections = accountSections();
  assert.ok(sections.length >= 10);
  const used = new Set();
  for (const { file, title, pane, entry } of sections) {
    assert.ok(pane, `${file}: the account section "${title}" has no prefs={SECTION_PREFS.<pane>["<title>"]} next to its scope`);
    assert.equal(entry, title, `${file}: the section "${title}" passes the entry of "${entry}"`);
    assert.ok(SECTION_PREFS[pane]?.[entry], `${file}: SECTION_PREFS.${pane}["${entry}"] does not exist`);
    const key = `${pane}/${entry}`;
    assert.ok(!used.has(key), `${key} is used twice`);
    used.add(key);
  }
  const table = Object.entries(SECTION_PREFS).flatMap(([pane, s]) => Object.keys(s).map((t) => `${pane}/${t}`));
  assert.deepEqual(table.filter((key) => !used.has(key)), [], "table entries no section uses");
});

test("every account preference is held by a section, or listed as untagged", () => {
  const held = new Set([...Object.values(SECTION_PREFS).flatMap((s) => Object.values(s).flat()), ...UNTAGGED_PREFS]);
  assert.deepEqual(ACCOUNT_PREFS.filter((name) => !held.has(name)), []);
});
