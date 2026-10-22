// The browser suite's change-based selection (tests/e2e/select.mjs): the
// groups cover every scenario file once, the rules name real groups, and
// every source file is placed by a rule of its own, never by the final
// catch-all that selects nothing; and the shards CI splits the suite into.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { GROUPS, RULES, ruleFor, selectGroups, shardOf } from "./e2e/select.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const ids = GROUPS.map((g) => g.id);

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
    if (entry.name === "__pycache__" || entry.name === "node_modules") continue;
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...walk(rel));
    else out.push(rel);
  }
  return out;
}

test("every scenario file is in exactly one group, and every group's files exist", () => {
  const scenarios = fs.readdirSync(path.join(ROOT, "frontend/tests/e2e/scenarios")).filter((f) => f.endsWith(".mjs"));
  const listed = GROUPS.flatMap((g) => g.files);
  assert.deepEqual([...listed].sort(), [...scenarios].sort());
  assert.equal(new Set(ids).size, ids.length, "group ids are unique");
});

test("rules name only known groups", () => {
  for (const [glob, groups] of RULES) {
    if (groups === "all") continue;
    for (const id of groups) assert.ok(ids.includes(id), `${glob} names unknown group ${id}`);
  }
});

test("every app source file is placed by a rule, not the catch-all", () => {
  const files = [...walk("frontend/src"), ...walk("frontend/public"), ...walk("backend/gamma")];
  const unplaced = files.filter((f) => ruleFor(f).glob === "**");
  assert.deepEqual(unplaced, [], "add a rule in tests/e2e/select.mjs for these");
});

test("a change selects the groups that exercise it", () => {
  const pick = (files, lines) => selectGroups(files, lines).groups;
  assert.deepEqual(pick(["frontend/src/guide/useGuide.js"]).sort(), ["contextual-guide", "guide", "triggered-guide"]);
  assert.deepEqual(pick(["docs/dev/debugging.md", "desktop/main.js", "backend/tests/test_ink.py"]), []);
  assert.equal(pick(["frontend/src/app/App.jsx"]).length, GROUPS.length);
  // the pens' "Use this browser's pens instead?" (prefs.js useProfileSync, App's onReplaced) is ink.mjs's
  for (const file of ["frontend/src/app/prefs.js", "frontend/src/app/prefDefs.js"]) assert.ok(pick([file]).includes("ink"), file);
  assert.deepEqual(pick(["backend/gamma/ink.py"]).sort(), ["ink", "ink-editing", "ink-shapes", "notebooks", "triggered-guide"]);
  // the Notability import is an export's groups; its fixture writer is the transfers scenario's
  assert.deepEqual(pick(["backend/gamma/notability_import.py"]).sort(), ["diagrams", "files", "ink", "transfers"]);
  assert.deepEqual(pick(["backend/tests/notability_fixtures.py"]), ["transfers"]);
  // a comment card stands in the page's box, where the ink layer and the text boxes leave its presses alone
  for (const file of ["frontend/src/ink/InkLayer.jsx", "frontend/src/markup/TextBoxLayer.jsx"]) assert.ok(pick([file]).includes("comments"), file);
  // the chat's pictures of handwriting are the AI modules' groups
  assert.deepEqual(pick(["backend/gamma/ink_view.py"]).sort(), pick(["backend/gamma/ai_context.py"]).sort());
  // a scenario helper selects the scenarios importing it
  const notes = pick(["frontend/tests/e2e/scenarios/notes.mjs"]);
  for (const id of ["notes-pdf-share", "collab", "files", "diagrams"]) assert.ok(notes.includes(id), id);
  // a guide anchor on a changed line brings the tours in, whatever the file
  const anchored = pick(["frontend/src/sharing/SharePopover.jsx"], () => ['+  <button data-guide="share.copy">']);
  for (const id of ["guide", "contextual-guide", "triggered-guide", "publish"]) assert.ok(anchored.includes(id), id);
  assert.ok(!pick(["frontend/src/sharing/SharePopover.jsx"], () => ["+  <button>"]).includes("guide"));
});

test("the shards split a selection whole, in shares of about equal time", () => {
  const time = Object.fromEntries(GROUPS.map((g) => [g.id, g.s]));
  assert.ok(ids.every((id) => time[id] > 0), "every group has its time");
  for (const n of [1, 2, 3]) {
    const shards = Array.from({ length: n }, (_, k) => shardOf(ids, k + 1, n));
    assert.deepEqual(shards.flat().sort(), [...ids].sort(), `${n} shards: every group once`);
    const loads = shards.map((s) => s.reduce((sum, id) => sum + time[id], 0));
    assert.ok(Math.max(...loads) - Math.min(...loads) <= Math.max(...Object.values(time)), `${n} shards: ${loads}`);
  }
  // a shard keeps the selection's order
  const picked = ["guide", "ink", "trash", "settings"];
  const mine = shardOf(picked, 1, 2);
  assert.deepEqual(mine, picked.filter((id) => mine.includes(id)));
});
