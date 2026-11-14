// What App's menus, drops, open page and list view ask of the work-folder
// rules (explorer/workFolderActions.js), over workfiling.json's main tree:
// the refusals, what filing leaves out, a page menu's rows, a copy's
// properties, the library requests and their words, the directories'
// counts, the list's rows and Let go.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  directoryMoves, emptyKind, fileName, fileRow, filingOf, foldersMoveRefused, letGoSaid, libraryCopy, libraryRequest, libraryRows, librarySaid,
  keptFolders, libraryTask, listDirs, listRows, mayLetGo, openFile, pagesMenu, refusesFiles, workCounts,
} from "../src/explorer/workFolderActions.js";
import { refusalText, treeOf } from "../src/explorer/workFolders.js";
import { libraryTree, pageBlocksOf } from "../src/library/libraryUtils.js";

const FILING = JSON.parse(readFileSync(new URL("../../tests/shared/workfiling.json", import.meta.url), "utf8"));

function listingOf(nodes) {
  const blocks = new Map(nodes.map((n) => [n.id, { id: n.id, content: n.id, position: "",
    properties: "vault" in n ? { vault: n.vault } : {}, children: [] }]));
  const top = [];
  for (const n of nodes) (n.parent ? blocks.get(n.parent).children : top).push(blocks.get(n.id));
  return { folders: { children: top } };
}
const lib = { ...libraryTree(listingOf(FILING.trees.main)), workCounts: { D1: 3, D1a: 2, D2: 1, W1: 4, D4: 5, L1: 9 } };
const tree = treeOf(lib);

const pdf = (name) => ({ format: "pdf", name, hash: "p1", ext: ".pdf", at: "path" });
const pages = new Map(pageBlocksOf([
  { id: "page", content: "Paper", properties: { folders: ["L1"], doc: { ...pdf("paper.pdf"), at: "store" } } },
  { id: "inner", content: "Inside", parent: "page", properties: {} },
  { id: "figure", content: "fig1.pdf", properties: { work_folder: "W1", file_name: "fig1.pdf", folders: ["D1"], doc: pdf("fig1.pdf") } },
  { id: "paper", content: "Attention", properties: { work_folder: "W1", file_name: "main.pdf", library: true, folders: ["D1", "L1"], doc: pdf("main.pdf") } },
  { id: "note", content: "notes", properties: { work_folder: "W1", file_name: "notes.md", folders: ["D1"] } },
].map((b) => ({ ...b, _parentPage: b.parent || "" })), lib).map((b) => [b.id, b]));

test("an action naming a file the rule refuses is refused; a library page never is", () => {
  assert.equal(refusesFiles(pages, ["page", "figure"], (r) => r.rename, tree), true, "a file is named by its file");
  assert.equal(refusesFiles(pages, ["page", "paper"], (r) => r.rename, tree), false, "in the library, renamed");
  assert.equal(refusesFiles(pages, ["page", "gone"], (r) => r.trash, tree), false, "a row the app lacks is no file");
  assert.equal(refusesFiles(pages, ["note"], (r) => r.trash, tree), false, "a note that follows goes to Recently deleted");
  assert.equal(fileRow(pages, "figure")?.id, "figure");
  assert.equal(fileRow(pages, "page"), null);
  assert.equal(fileRow(pages, "gone"), null);
});

test("no folder goes into a work folder, and its directories stay where the disk has them", () => {
  assert.equal(foldersMoveRefused(tree, ["L1a"], "L2"), false);
  assert.equal(foldersMoveRefused(tree, ["L1a"], "D1"), true, "into a directory");
  assert.equal(foldersMoveRefused(tree, ["L1a", "D1a"], ""), true, "a directory moved");
  assert.equal(foldersMoveRefused(tree, ["W1"], "L2"), false, "W out of every work folder");
  assert.equal(foldersMoveRefused(tree, [], "D1"), false, "nothing to move");
});

test("the open page: its row's rules, else the page read's; its title the file's", () => {
  const figure = openFile(pages.get("figure"), { properties: { work_folder: "W1" } }, tree);
  assert.deepEqual([figure.work, figure.titleLocked, figure.inLibrary, figure.rules.trash], ["W1", true, false, false]);
  const paper = openFile(undefined, { properties: { work_folder: "W1", library: true, folders: ["D1"], doc: pdf("main.pdf") } }, tree);
  assert.deepEqual([paper.work, paper.titleLocked, paper.inLibrary], ["W1", false, true]);
  const page = openFile(pages.get("page"), { properties: {} }, tree);
  assert.deepEqual([page.work, page.titleLocked, page.rules.trash], ["", false, true]);
});

test("a copy of a work folder's note is a library page, in the library folders it was in", () => {
  const props = { work_folder: "W1", file_name: "notes.md", library: true, folders: ["D1", "L1", "Gone"], color: "red" };
  assert.deepEqual(libraryCopy(props, tree), { folders: ["L1"], color: "red" });
  assert.deepEqual(libraryCopy({ work_folder: "W1", folders: ["D1"] }, tree), {}, "no library folder: none");
  const plain = { folders: ["L1"] };
  assert.equal(libraryCopy(plain, tree), plain, "a library page's are kept as they are");
});

test("filing leaves out a page inside another and a file not in the library", () => {
  assert.deepEqual(filingOf(pages, ["page", "paper"]), { ids: ["page", "paper"], left: "", why: "" });
  const some = filingOf(pages, ["page", "inner", "figure"]);
  assert.deepEqual(some.ids, ["page"]);
  assert.match(some.left, /^ Left out 1 page inside another page.* Left out 1 work folder's file/);
  assert.deepEqual(filingOf(pages, ["figure"]), { ids: [], left: "", why: refusalText("filed") });
  assert.deepEqual(filingOf(pages, ["inner"]), { ids: [], left: "", why: "A page inside another page is filed with it." });
  assert.deepEqual(filingOf(pages, ["gone"]).ids, ["gone"], "a row the app lacks goes with the write");
});

test("into a directory only a note that follows moves", () => {
  assert.deepEqual([...directoryMoves(pages, ["note"], "D2", tree).into], [["note", ["D2"]]]);
  assert.deepEqual(directoryMoves(pages, ["note", "figure"], "D2", tree), { into: null, why: refusalText("filed") });
  assert.deepEqual(directoryMoves(pages, ["page"], "D2", tree), { into: null, why: refusalText("directories") });
  assert.deepEqual(directoryMoves(pages, ["gone"], "D2", tree), { into: null, why: refusalText("directories") });
});

test("a page menu: the files among the pages, the rules they all keep, what Move to folder files", () => {
  const ids = ["page", "inner", "paper"];
  const menu = pagesMenu(ids.map((id) => pages.get(id)), ids, pages, tree);
  assert.deepEqual(menu.files.map((b) => b.id), ["paper"]);
  assert.deepEqual([menu.allFiles, menu.anyFile, menu.ruled("rename"), menu.ruled("trash")], [false, true, true, false]);
  assert.deepEqual(menu.filable.map((b) => b.id), ["page", "paper"]);
  assert.equal(menu.leftOut, 1);
  assert.deepEqual(menu.ownFolders, ["L1"], "never a file's directory");
  assert.deepEqual(menu.copied, ["page", "inner"], "a file's PDF is copied by Save a copy");
  const files = pagesMenu([pages.get("figure"), undefined], ["figure", "gone"], pages, tree);
  assert.deepEqual([files.allFiles, files.filable.length, files.leftOut, files.copied], [false, 0, 1, ["gone"]]);
});

test("the library rows: Add while none is in it, Remove while all are, Save a copy for PDFs", () => {
  assert.deepEqual(libraryRows([pages.get("figure")], tree), { ids: ["figure"], add: true, remove: false, copy: true });
  assert.deepEqual(libraryRows([pages.get("paper")], tree), { ids: ["paper"], add: false, remove: true, copy: false });
  assert.deepEqual(libraryRows([pages.get("figure"), pages.get("note")], tree), { ids: ["figure", "note"], add: true, remove: false, copy: false });
  assert.deepEqual(libraryRows([pages.get("figure"), pages.get("paper")], tree), { ids: ["figure", "paper"], add: false, remove: false, copy: false });
});

test("the library requests, one a page, and what they say", () => {
  assert.deepEqual(libraryRequest("a/b", "remove"), { path: "/pages/a%2Fb/library", init: { method: "DELETE" } });
  const add = libraryRequest("x", "add", "L1");
  assert.equal(add.path, "/pages/x/library");
  assert.deepEqual([add.init.method, JSON.parse(add.init.body)], ["POST", { folder: "L1" }]);
  const copy = libraryRequest("x", "copy");
  assert.deepEqual([copy.path, JSON.parse(copy.init.body)], ["/pages/x/library-copy", {}]);
  assert.equal(libraryTask("remove", 3), "Remove 3 files from the library");
  assert.equal(libraryTask("copy", 3), "Save copies of 3 files to the library");
  assert.equal(libraryTask("add", 3), "Add 3 files to the library");
  assert.equal(librarySaid("remove", 1), "Removed 1 file from the library.");
  assert.equal(librarySaid("add", 2), "Added 2 files to the library.");
  assert.equal(librarySaid("copy", 3, 1), "Saved copies of 2 files to the library. 1 was in the library already.");
  assert.equal(librarySaid("copy", 1), "Saved a copy of 1 file to the library.");
});

test("a work folder's folders count the files below them, the library's their pages", () => {
  const { inWork, counts } = workCounts(tree);
  assert.deepEqual([...inWork].sort(), ["D1", "D1a", "D2", "D3", "D4", "W1", "W2", "W3"]);
  assert.deepEqual(Object.fromEntries(counts), { D1: 5, D1a: 2, D2: 1, W1: 10, D4: 5, W3: 5 }, "never a library folder above W3");
});

test("the list: its directories by name, its known rows by the filter's name, what an empty list says", () => {
  const named = { ...lib, folders: new Map([...lib.folders].map(([id, f]) => [id, { ...f, name: { D1: "b10", D2: "b9" }[id] || f.name }])) };
  assert.deepEqual(listDirs(["D1", "D2"], named, { D1: { count: 5 } }), [{ id: "D2", name: "b9", count: 0 }, { id: "D1", name: "b10", count: 5 }]);
  const rows = [{ id: "figure", open_comments: 2 }, { id: "gone" }, { id: "note" }];
  assert.deepEqual(listRows(rows, pages, "").map((b) => [b.id, b.open_comments]), [["figure", 2], ["note", 0]]);
  assert.deepEqual(listRows(rows, pages, " FIG ").map((b) => b.id), ["figure"]);
  assert.equal(fileName({ _fileName: "", content: "" }), "Untitled");
  assert.equal(emptyKind({ choosing: true, query: "x", loaded: 3 }), "choosing");
  assert.equal(emptyKind({ query: "x", loaded: 3, atTop: true }), "nomatch");
  assert.equal(emptyKind({ atTop: true }), "untracked");
  assert.equal(emptyKind({ query: "x" }), "nomatch");
  assert.equal(emptyKind({}), "none");
});

test("Let go: a work folder no vault of this server keeps, for the workspace's owner", () => {
  assert.equal(mayLetGo(tree, "W1", { owner: true }), true);
  assert.equal(mayLetGo(tree, "W3", { owner: true }), true, "a work folder inside a library folder");
  assert.equal(mayLetGo(tree, "W1", { owner: false }), false, "an editor's menu has none");
  assert.equal(mayLetGo(tree, "W1", { owner: true, kept: new Set(["W1"]) }), false, "a vault of this server keeps it: remove that instead");
  assert.equal(mayLetGo(tree, "W1", { owner: true, kept: new Set(["W3"]) }), true, "another folder's vault");
  assert.equal(mayLetGo(tree, "W1", { owner: true, kept: null }), false, "the desktop app's own server has not said yet");
  const links = [
    { kind: "folder", folder_id: "W1", remote_url: "", mine: false, workspace_id: "ws2" },
    { kind: "folder", folder_id: "W2", remote_url: "", mine: true },
    { kind: "folder", folder_id: "W3", remote_url: "https://nas.example/", mine: true },
    { kind: "file", page_id: "p1", remote_url: "" },
  ];
  assert.deepEqual([...keptFolders(links)], ["W1", "W2"], "another member's vault counts; a vault of another server does not");
  assert.equal(mayLetGo(tree, "W1", { owner: true, kept: keptFolders(links) }), false);
  assert.deepEqual([...keptFolders(undefined)], []);
  assert.equal(mayLetGo(tree, "D1", { owner: true }), false, "a directory carries no mark");
  assert.equal(mayLetGo(tree, "L1", { owner: true }), false, "a library folder");
  assert.equal(mayLetGo(tree, "", { owner: true }), false);
  assert.equal(letGoSaid(0), "Let go of the work folder: its files stay in the folder.");
  assert.equal(letGoSaid(1), "Let go of the work folder: 1 file went to Recently deleted.");
  assert.equal(letGoSaid(3), "Let go of the work folder: 3 files went to Recently deleted.");
});
