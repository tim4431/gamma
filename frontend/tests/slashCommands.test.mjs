// The "/" menu's catalog and its matching tiers (editor/slashCommands.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import { SLASH_COMMANDS, SLASH_GROUPS, filterSlashCommands, fuzzyIndices } from "../src/editor/slashCommands.js";

const ALL = { newPage: true, sheet: true };
const names = (q, can = ALL) => filterSlashCommands(q, can).map((c) => c.name);

test("the bare / lists every command but the colors, group by group", () => {
  const bare = filterSlashCommands("", ALL);
  assert.equal(bare[0].name, "h1");
  assert.ok(bare.every((c) => !c.hidden && !c.match));
  const groups = bare.map((c) => c.group).filter((g, i, a) => g !== a[i - 1]);
  assert.deepEqual(groups, Object.keys(SLASH_GROUPS), "each group in one run, in the titles' order");
});

test("every command names a group, an icon or glyph, and a description", () => {
  for (const c of SLASH_COMMANDS) {
    assert.ok(SLASH_GROUPS[c.group], c.name);
    assert.ok(c.icon || c.glyph, c.name);
    assert.ok(c.hint, c.name);
    assert.match(c.name, /^[a-z0-9-]+$/, "a name the / trigger can type");
  }
});

test("a command's own name first, then names it begins, then label words, then keywords", () => {
  // "/note" is the sheet, not the links or the callout that mention notes.
  assert.deepEqual(names("note"), ["note", "link", "embed", "callout"]);
  assert.equal(names("page")[0], "page");
  assert.ok(names("page").includes("note"), "page is one of the sheet's words");
  assert.deepEqual(names("h").slice(0, 4), ["h1", "h2", "h3", "highlight"], "ties keep the bare list's order");
  assert.deepEqual(names("list"), ["bullet", "number"]);
  assert.equal(names("checkbox")[0], "todo");
  assert.equal(names("formula")[0], "math");
  assert.deepEqual(names("red"), ["red-text", "red-background"], "colors show once typed");
});

test("fuzzy matches jump to word starts, VS Code style", () => {
  assert.equal(names("hn")[0], "note", "Handwritten Note");
  assert.equal(names("cb")[0], "code", "Code Block");
  assert.equal(names("nl")[0], "number", "Numbered List");
  assert.equal(names("eb")[0], "equation", "Equation Block before the looser embed");
  assert.equal(names("tbl")[0], "table");
  assert.equal(names("mmd")[0], "mermaid");
  assert.equal(names("rb")[0], "red-background");
  assert.deepEqual(names("zzz"), []);
  assert.deepEqual(fuzzyIndices("handwritten note", "hn").at, [0, 12]);
  assert.deepEqual(fuzzyIndices("bulleted list", "ble").at, [0, 2, 4], "falls back when word jumps strand a letter");
  assert.equal(fuzzyIndices("note", "n"), null, "one letter is a prefix query, not fuzzy");
  assert.equal(fuzzyIndices("note", "ot"), null, "anchored on the first letter");
});

test("the match says which letters to mark, in the name or the label", () => {
  const [note] = filterSlashCommands("no", ALL);
  assert.deepEqual(note.match.name, [0, 1]);
  const hn = filterSlashCommands("hn", ALL)[0];
  assert.deepEqual(hn.match.label, [0, 12]);
  assert.equal(hn.match.name, undefined);
  const todo = filterSlashCommands("checkbox", ALL)[0];
  assert.equal(todo.match.name ?? todo.match.label, undefined, "a keyword hit marks nothing");
});

test("a command the editor cannot run is left out", () => {
  assert.ok(!names("page", {}).includes("page"));
  assert.ok(!names("note", {}).includes("note"));
  assert.ok(!filterSlashCommands("", {}).some((c) => c.needs));
});
