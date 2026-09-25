// The command catalogs (app/commands.js) and what keeps them honest: unique
// ids, no two defaults on one chord within a scope, every default chord in
// the user guide's cheat sheet, and the pure helpers the block commands
// call (blockModel's sibling move / delete-keeping-children / neighbour,
// mdMarks' to-do toggle).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ALL_COMMANDS, GROUPS, fixedKeys } from "../src/app/commands.js";
import { APP_COMMANDS } from "../src/app/appCommands.js";
import { BLOCK_COMMANDS } from "../src/editor/blockCommands.js";
import { chordLabel, conflicts, effectiveKeys, normalizeChord } from "../src/shared/lib/hotkeys.js";
import { moveSibling, removeBlockKeepChildren, visibleNeighbor } from "../src/shared/model/blockModel.js";
import { toggleTodoLine } from "../src/editor/mdMarks.js";

const GUIDE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../../docs/user_guide.md");

test("every command has a unique id, a label, a known group and canonical default keys", () => {
  const ids = new Set();
  for (const cmd of ALL_COMMANDS) {
    assert.ok(cmd.id && !ids.has(cmd.id), `duplicate or missing id: ${cmd.id}`);
    ids.add(cmd.id);
    assert.ok(cmd.label, `${cmd.id} has no label`);
    assert.ok(GROUPS.includes(cmd.group), `${cmd.id} is in an unknown group "${cmd.group}"`);
    assert.equal(typeof cmd.run, "function", `${cmd.id} has no run`);
    for (const k of effectiveKeys(cmd, {})) assert.equal(k, normalizeChord(k), `${cmd.id}: "${k}" is not canonical`);
  }
  const byId = (id) => ALL_COMMANDS.find((c) => c.id === id);
  assert.equal(byId("block.deleteLine")?.keys, "Mod-Shift-k");
  assert.equal(byId("app.renameTitle")?.keys, "F2");
});

test("no two defaults share a chord within a scope", () => {
  for (const [name, list] of [["app", APP_COMMANDS], ["block", BLOCK_COMMANDS]]) {
    const clash = conflicts(list, {});
    assert.equal(clash.size, 0, `${name}: ${[...clash.entries()].map(([k, v]) => `${k} → ${v.map((c) => c.id).join(", ")}`).join("; ")}`);
  }
});

test("the user guide's cheat sheet names every default shortcut", () => {
  const guide = fs.readFileSync(GUIDE, "utf8");
  const sheet = guide.slice(guide.indexOf("## Shortcut cheat sheet"));
  assert.ok(sheet.length > 100, "the cheat sheet section exists");
  for (const cmd of ALL_COMMANDS) {
    for (const k of effectiveKeys(cmd, {})) {
      const label = chordLabel(k, false);
      assert.ok(sheet.includes(label), `${cmd.id}: "${label}" is missing from the cheat sheet`);
    }
  }
  for (const [chords] of fixedKeys(false)) {
    for (const c of chords) assert.ok(sheet.includes(chordLabel(c, false)), `built-in "${chordLabel(c, false)}" is missing from the cheat sheet`);
  }
});

test("fixedKeys follows the Enter preference", () => {
  assert.deepEqual(fixedKeys(false)[0][0], ["Shift-Enter"]);
  assert.deepEqual(fixedKeys(true)[0][0], ["Enter"]);
});

const tree = () => [
  { id: "a", content: "a", children: [{ id: "a1", content: "a1", children: [] }, { id: "a2", content: "a2", collapsed: true, children: [{ id: "a2x", content: "x", children: [] }] }] },
  { id: "b", content: "b", children: [] },
];
const ids = (list) => list.map((b) => b.id + (b.children?.length ? `(${ids(b.children)})` : "")).join(",");

test("moveSibling swaps with the neighbour and stays put at the edges", () => {
  const t = tree();
  assert.equal(ids(moveSibling(t, "a2", -1)), "a(a2(a2x),a1),b");
  assert.equal(ids(moveSibling(t, "a", 1)), "b,a(a1,a2(a2x))");
  assert.equal(moveSibling(t, "b", 1), t, "same reference at the bottom");
  assert.equal(moveSibling(t, "a1", -1), t, "same reference at the top of its parent");
  assert.equal(moveSibling(t, "zzz", 1), t, "same reference for an unknown id");
});

test("removeBlockKeepChildren lifts the children into the block's place", () => {
  assert.equal(ids(removeBlockKeepChildren(tree(), "a")), "a1,a2(a2x),b");
  assert.equal(ids(removeBlockKeepChildren(tree(), "a2")), "a(a1,a2x),b");
  assert.equal(ids(removeBlockKeepChildren(tree(), "b")), "a(a1,a2(a2x))");
});

test("visibleNeighbor walks the outliner as shown, collapsed subtrees skipped", () => {
  const t = tree();
  assert.equal(visibleNeighbor(t, "a", 1)?.id, "a1");
  assert.equal(visibleNeighbor(t, "a2", 1)?.id, "b", "a2 is collapsed: its child is not shown");
  assert.equal(visibleNeighbor(t, "b", -1)?.id, "a2");
  assert.equal(visibleNeighbor(t, "a", -1), null);
  assert.equal(visibleNeighbor(t, "b", 1), null);
});

test("toggleTodoLine adds, checks and unchecks a checkbox on the caret's line", () => {
  assert.equal(toggleTodoLine("hello", 3).text, "- [ ] hello");
  assert.equal(toggleTodoLine("- [ ] task", 8).text, "- [x] task");
  assert.equal(toggleTodoLine("- [x] task", 8).text, "- [ ] task");
  assert.equal(toggleTodoLine("  - item", 5).text, "  - [ ] item");
  assert.equal(toggleTodoLine("1. step", 3).text, "1. [ ] step");
  const r = toggleTodoLine("a\n- [ ] b\nc", 4);
  assert.equal(r.text, "a\n- [x] b\nc");
  assert.deepEqual([r.from, r.to, r.insert], [2, 9, "- [x] b"]);
  // The caret moves with the inserted marker, never before its line.
  assert.equal(toggleTodoLine("hello", 0).pos, 6);
  assert.equal(toggleTodoLine("hello", 5).pos, 11);
});
