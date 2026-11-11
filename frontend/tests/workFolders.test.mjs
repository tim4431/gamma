// The work folders' rules on the client (explorer/workFolders.js): the
// folder tree as the rules read it from the listing, the op rule's twin
// over workfiling.json (each S, R and T row, per writer, against
// gamma/work_folders/filing.py's answers), and what the menus and drops
// offer for a file and for a folder.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  WORDS, creationOf, fileRules, filingTargets, folderRules, follows, isWorkFile, markOf, mayRestore,
  mayTrash, refusalText, rootPatchAllowed, treeOf, treeOpAllowed, workOf,
} from "../src/explorer/workFolders.js";
import { libraryTree, pageBlocksOf } from "../src/library/libraryUtils.js";

const FILING = JSON.parse(readFileSync(new URL("../../tests/shared/workfiling.json", import.meta.url), "utf8"));

// A fixture's tree as the listing carries it (nested, the mark in the
// folder block's properties), read as the app reads it.
function listingOf(nodes) {
  const blocks = new Map(nodes.map((n) => [n.id, { id: n.id, content: n.id, position: "",
    properties: "vault" in n ? { vault: n.vault } : {}, children: [] }]));
  const top = [];
  for (const n of nodes) (n.parent ? blocks.get(n.parent).children : top).push(blocks.get(n.id));
  return { folders: { children: top } };
}
const LIB = Object.fromEntries(Object.entries(FILING.trees).map(([name, nodes]) => [name, libraryTree(listingOf(nodes))]));
const TREES = Object.fromEntries(Object.entries(LIB).map(([name, lib]) => [name, treeOf(lib)]));

function judged(c, got) {
  assert.equal(got.allowed, c.allowed, `${c.name}: allowed`);
  if (!c.allowed) {
    assert.equal(got.kind, c.kind, `${c.name}: kind`);
    assert.equal(got.why, refusalText(c.kind), `${c.name}: the words`);
    assert.ok(got.why, `${c.name}: words to read`);
  }
}

test("the op rule's twin answers each case of workfiling.json as filing.py does", () => {
  for (const c of FILING.patch) {
    const got = rootPatchAllowed(TREES[c.tree], c.before, c.patch, c.content ?? null, c.writer,
      { targetIsFile: c.target_is_file || false });
    judged(c, got);
    if (c.allowed) assert.deepEqual(got.value, "result" in c ? c.result : c.patch, `${c.name}: the patch stored`);
  }
  for (const c of FILING.create) {
    const got = creationOf(TREES[c.tree], c.props, c.writer, { targetIsFile: c.target_is_file || false });
    judged(c, got);
    if (c.allowed) assert.deepEqual(got.value, "result" in c ? c.result : c.props, `${c.name}: the props stored`);
  }
  for (const c of FILING.trash) judged(c, mayTrash(TREES[c.tree], c.props, c.writer));
  for (const c of FILING.restore) {
    const got = mayRestore(TREES[c.tree], c.props, c.writer);
    judged(c, got);
    if (c.allowed) assert.deepEqual(got.value, "result" in c ? c.result : c.props, `${c.name}: the props restored`);
  }
  for (const c of FILING.tree) judged(c, treeOpAllowed(TREES[c.tree], c.op, c.writer));
});

test("what the menus offer (fileRules) is what the op rule answers a person, case by case", () => {
  const person = (group) => FILING[group].filter((c) => c.writer === "person");
  for (const c of person("trash")) assert.equal(fileRules({ properties: c.props }, TREES[c.tree]).trash, c.allowed, c.name);
  for (const c of person("patch").filter((c) => c.content != null && !c.patch)) {
    assert.equal(fileRules({ properties: c.before }, TREES[c.tree]).rename, c.allowed, c.name);
  }
  for (const c of person("patch").filter((c) => Array.isArray(c.patch?.folders) && Object.keys(c.patch).length === 1)) {
    const was = c.before?.folders || [];
    const added = c.patch.folders.filter((f) => !was.includes(f));
    if (added.length !== 1 || c.patch.folders.length !== was.length + 1) continue;
    const refiled = fileRules({ properties: c.before }, TREES[c.tree]).refile(added[0]);
    assert.equal(!!refiled.folders, c.allowed, c.name);
  }
});

test("the twin leaves its input alone, and takes the listing's tree as it takes its own", () => {
  const before = { work_folder: "W1", file_name: "main.pdf", library: true, folders: ["D1", "L1"] };
  const patch = { library: null };
  assert.deepEqual(rootPatchAllowed(LIB.main, before, patch, null).value, { library: null, folders: ["D1"] });
  assert.deepEqual([patch, before.folders], [{ library: null }, ["D1", "L1"]]);
  assert.equal(treeOf(LIB.main), TREES.main, "made once per listing tree");
  assert.equal(treeOf(TREES.main), TREES.main);
  assert.equal(workOf(undefined, "D1"), "", "no tree yet: no work folder");
});

test("every refusal has the server's words", () => {
  const kinds = new Set(["patch", "create", "trash", "restore", "tree"].flatMap((g) => FILING[g]).filter((c) => !c.allowed).map((c) => c.kind));
  assert.deepEqual([...kinds].sort(), Object.keys(WORDS).sort());
  assert.equal(refusalText("directories"), "A work folder's directories follow the disk.");
  assert.equal(refusalText("nonsense"), "");
});

test("W(f) is the nearest marked folder on the chain; the filing targets are the folders in none", () => {
  const tree = TREES.main;
  assert.deepEqual(["W1", "D1", "D1a", "D2", "D4", "H", "L1a", "Gone", ""].map((f) => workOf(tree, f)),
    ["W1", "W1", "W1", "W1", "W3", "", "", "", ""]);
  assert.deepEqual(markOf(tree, "W2"), { pull: false, library: true });
  assert.equal(markOf(tree, "D1"), null, "only the root carries the mark");
  assert.deepEqual(filingTargets(tree), ["L1", "L1a", "L2", "H"]);
  assert.equal(workOf(TREES.released, "D1"), "", "its mark gone: a library folder again");
});

// Rows of App's page list over the fixture's tree.
const pdf = (name) => ({ format: "pdf", name, hash: "p1", ext: ".pdf", at: "path" });
const rows = new Map(pageBlocksOf([
  { id: "page", content: "Paper", properties: { folders: ["L1"], doc: { ...pdf("paper.pdf"), at: "store" } } },
  { id: "opened", content: "read.pdf", properties: { doc: pdf("read.pdf") } },
  { id: "figure", content: "fig1.pdf", properties: { work_folder: "W1", file_name: "fig1.pdf", folders: ["D1"], doc: pdf("fig1.pdf") } },
  { id: "paper", content: "Attention", properties: { work_folder: "W1", file_name: "main.pdf", library: true, folders: ["D1", "L1"], doc: pdf("main.pdf") } },
  { id: "note", content: "notes", properties: { work_folder: "W1", file_name: "notes.md", folders: ["D1"] } },
  { id: "kept", content: "a", properties: { work_folder: "W2", file_name: "a.md", folders: ["D3"] } },
].map((b) => ({ ...b, _parentPage: "" })), LIB.main).map((b) => [b.id, b]));
const rules = (id) => fileRules(rows.get(id), TREES.main);

test("a library page may do all but what only a file has", () => {
  const page = rules("page");
  assert.deepEqual([page.rename, page.trash, page.restore, page.moveIntoPage, page.duplicate], [true, true, true, true, true]);
  assert.deepEqual([page.addToLibrary, page.removeFromLibrary, page.saveCopy], [false, false, false]);
  assert.deepEqual(page.refileTargets(), ["L1", "L1a", "L2", "H"], "never a work folder's directory");
  assert.deepEqual(page.removableFolders, ["L1"]);
  assert.deepEqual(page.refile("L1a"), { folders: ["L1a"], why: "" }, "into a subfolder: the one above gives way");
  assert.deepEqual(page.refile("D1"), { folders: null, why: refusalText("directories") });
  assert.equal(rules("opened").duplicate, false, "a PDF read in place has no bytes to copy");
});

test("a file not in the library: added, copied, never renamed, refiled or deleted", () => {
  const figure = rules("figure");
  assert.deepEqual([figure.rename, figure.trash, figure.restore, figure.moveIntoPage, figure.duplicate], [false, false, false, false, false]);
  assert.deepEqual([figure.addToLibrary, figure.removeFromLibrary, figure.saveCopy], [true, false, true]);
  assert.deepEqual([figure.refileTargets(), figure.removableFolders], [[], []]);
  assert.deepEqual(figure.refile("D2"), { folders: null, why: refusalText("filed") }, "a PDF follows its file");
  assert.deepEqual(figure.refile("L1"), { folders: null, why: refusalText("filed") }, "in library folders only once in the library");
});

test("a file in the library: renamed, filed in library folders too, its directory kept", () => {
  const paper = rules("paper");
  assert.deepEqual([paper.rename, paper.trash, paper.moveIntoPage, paper.duplicate], [true, false, false, false]);
  assert.deepEqual([paper.addToLibrary, paper.removeFromLibrary, paper.saveCopy], [false, true, false], "a copy of its own page would be itself");
  assert.deepEqual(paper.refileTargets(), ["L1", "L1a", "L2", "H"]);
  assert.deepEqual(paper.removableFolders, ["L1"], "never its directory");
  assert.deepEqual(paper.refile("L2").folders, ["D1", "L1", "L2"]);
  assert.deepEqual(paper.refile("L1a").folders, ["D1", "L1a"]);
  assert.equal(paper.refile("D2").folders, null, "its directory is its file's");
  assert.equal(paper.refile("D4").folders, null, "nor another work folder's");
});

test("a note follows where its work folder pulls: renamed, moved between its directories, trashed", () => {
  const note = rules("note");
  assert.equal(follows(rows.get("note"), TREES.main), true);
  assert.deepEqual([note.rename, note.trash, note.restore, note.duplicate], [true, true, true, true]);
  assert.deepEqual(note.refile("D2"), { folders: ["D2"], why: "" });
  assert.deepEqual(note.refile("W1").folders, ["W1"], "to its work folder's top");
  assert.equal(note.refile("D3").folders, null, "never into another work folder");
  assert.equal(note.refile("L1").folders, null);
  const kept = rules("kept");
  assert.equal(follows(rows.get("kept"), TREES.main), false, "a work folder that does not pull");
  assert.deepEqual([kept.rename, kept.trash, kept.restore, kept.duplicate], [false, false, false, true]);
  assert.equal(kept.refile("W2").folders, null);
});

test("a block says the same as its row: the open page's", () => {
  const block = { id: "figure", properties: { work_folder: "W1", file_name: "fig1.pdf", folders: ["D1"], doc: pdf("fig1.pdf") } };
  assert.equal(isWorkFile(block), true);
  assert.equal(isWorkFile(rows.get("figure")), true);
  assert.equal(isWorkFile(rows.get("page")), false);
  assert.equal(isWorkFile({ properties: { work_folder: "" } }), false);
  assert.equal(isWorkFile(null), false);
  const { refile: _refile, refileTargets: _targets, ...fromBlock } = fileRules(block, TREES.main);
  const { refile: _rowRefile, refileTargets: _rowTargets, ...fromRow } = rules("figure");
  assert.deepEqual(fromBlock, fromRow);
  assert.equal(follows({ properties: { work_folder: "W1", folders: ["D1"] } }, LIB.main), true, "the listing's tree as well");
});

test("a folder: W renamed and moved out of every work folder, its directories left to the disk", () => {
  const folder = (id) => folderRules(TREES.main, id);
  assert.deepEqual(folder("L1"), { rename: true, move: true, moveTargets: ["L2", "H"], delete: true, newSubfolder: true,
    newPage: true, chooseFiles: false });
  assert.equal(folder("H").delete, false, "it holds a work folder");
  assert.deepEqual(folder("H").moveTargets, ["L1", "L1a", "L2"]);
  assert.deepEqual(folder("W1"), { rename: true, move: true, moveTargets: ["L1", "L1a", "L2", "H"], delete: false,
    newSubfolder: false, newPage: true, chooseFiles: true });
  assert.equal(folder("W2").newPage, false, "a work folder that does not pull makes no note");
  assert.deepEqual(folder("D1a"), { rename: false, move: false, moveTargets: [], delete: false, newSubfolder: false,
    newPage: true, chooseFiles: false });
  assert.equal(folder("D3").newPage, false);
  assert.deepEqual(folder(""), { rename: false, move: false, moveTargets: [], delete: false, newSubfolder: true,
    newPage: true, chooseFiles: false }, "the library's top");
  assert.deepEqual(folderRules(LIB.released, "D1").newSubfolder, true, "its mark gone: a library folder");
});
