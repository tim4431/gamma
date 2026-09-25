// The browser suite's change-based selection (tests/e2e/select.mjs): the
// groups cover every scenario file once, the rules name real groups, and
// every source file is placed by a rule of its own, never by the final
// catch-all that selects nothing.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { GROUPS, RULES, ruleFor, selectGroups } from "./e2e/select.mjs";

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
  assert.deepEqual(pick(["backend/gamma/ink.py"]).sort(), ["ink", "ink-editing", "triggered-guide"]);
  // a scenario helper selects the scenarios importing it
  const notes = pick(["frontend/tests/e2e/scenarios/notes.mjs"]);
  for (const id of ["notes-pdf-share", "collab", "files", "mermaid"]) assert.ok(notes.includes(id), id);
  // a guide anchor on a changed line brings the tours in, whatever the file
  const anchored = pick(["frontend/src/sharing/SharePopover.jsx"], () => ['+  <button data-guide="share.copy">']);
  for (const id of ["guide", "contextual-guide", "triggered-guide", "publish"]) assert.ok(anchored.includes(id), id);
  assert.ok(!pick(["frontend/src/sharing/SharePopover.jsx"], () => ["+  <button>"]).includes("guide"));
});
