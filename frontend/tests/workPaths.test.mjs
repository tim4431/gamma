// A work folder's paths on the client (explorer/workPaths.js), held to
// "workpaths.json" (tests/shared/), the fixture gamma/work_folders/paths.py
// answers too (each case's path and directory): each case's crumbs, and
// which link addresses are paths relative to a note.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileCrumbs, fileNameOf, isRelativeHref } from "../src/explorer/workPaths.js";
import { treeOf } from "../src/explorer/workFolders.js";
import { libraryTree } from "../src/library/libraryUtils.js";

const FIXTURE = JSON.parse(readFileSync(new URL("../../tests/shared/workpaths.json", import.meta.url), "utf8"));

// The fixture's rows ([id, parent or "", name, mark or null]) as the
// listing nests them, read as the app reads it.
function treeFrom(rows) {
  const blocks = new Map(rows.map(([id, , name, mark]) => [id, { id, content: name, position: "",
    properties: mark === "vault" ? { vault: { pull: true } } : {}, children: [] }]));
  const top = [];
  for (const [id, parent] of rows) (parent ? blocks.get(parent).children : top).push(blocks.get(id));
  return libraryTree({ folders: { children: top } });
}
const LIB = treeFrom(FIXTURE.tree);
const WORK = treeOf(LIB);
const filedOf = (c) => ({ folders: c.props.folders || [], work: c.props.work_folder || "" });

test("each case's crumbs: W down to its directory, none where paths.py gives no path", () => {
  assert.ok(FIXTURE.cases.length);
  for (const c of FIXTURE.cases) {
    assert.deepEqual(fileCrumbs(LIB, WORK, filedOf(c)).map((f) => f.id), c.crumbs, `${c.name}: crumbs`);
    assert.equal(c.crumbs.length > 0, c.path !== "", `${c.name}: crumbs where there is a path`);
  }
});

test("a file row's name: its file's, else a note's title", () => {
  assert.equal(fileNameOf({ content: "fig1", properties: { file_name: "fig1.pdf" } }), "fig1.pdf");
  assert.equal(fileNameOf({ content: "Plan", properties: {} }), "Plan");
  assert.equal(fileNameOf({ properties: {} }), "");
});

test("the crumbs name their folders, W first", () => {
  const raw = FIXTURE.cases.find((c) => c.crumbs.length === 3);
  assert.deepEqual(fileCrumbs(LIB, WORK, filedOf(raw)).map((f) => f.name), ["Paper", "figures", "raw"]);
});

test("a link's address is relative where page_at_href would resolve it", () => {
  assert.ok(FIXTURE.hrefs.length);
  for (const h of FIXTURE.hrefs) assert.equal(isRelativeHref(h.href), h.relative, h.href);
  assert.equal(isRelativeHref(undefined), false);
  assert.equal(isRelativeHref("   "), false);
});
