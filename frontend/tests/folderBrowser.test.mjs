// The folder browser's rows (explorer/folderBrowser.js): a store's folders
// read a level at a time, the folders not made yet merged among the read
// ones by name, the new folder's field, a folder's files by name, and the
// note a folder says while it is read, after a failed read, empty or cut
// short; the folders to read next; and a new folder's name.
import assert from "node:assert/strict";
import { test } from "node:test";
import { browserRows, cleanName, nameProblem, pathsTo, unread } from "../src/explorer/folderBrowser.js";

const read = (folders, files = [], more = false) => ({ loaded: true, loading: false, error: "",
  folders: folders.map((name) => ({ name, modified: "" })), files: files.map((name) => ({ name, size: 1, modified: "" })), more });
const shape = (rows) => rows.map((r) => `${"  ".repeat(r.level - 1)}${r.kind}:${r.name}${r.made ? "" : " (not made)"}`);

test("the top, its folders by name with the ones not made yet among them, then files by name and the notes", () => {
  const reads = new Map([
    ["", read(["Gamma backups", "Archive 10", "Archive 9"], ["file10.zip", "file9.zip"])],
    ["Gamma backups", read([])],
  ]);
  const rows = browserRows({ root: "My Drive", reads, expanded: ["", "Gamma backups", "Backups", "Backups/Server A"],
    added: ["Backups", "Backups/Server A", "Gamma backups"] });
  assert.deepEqual(shape(rows), [
    "folder:My Drive",
    "  folder:Archive 9",
    "  folder:Archive 10",
    "  folder:Backups (not made)",
    "    folder:Server A (not made)",
    "      note:Not made yet: it is made when something is saved in it.",
    "  folder:Gamma backups", // read, so made: its name in `added` is not drawn twice
    "    note:This folder is empty.",
    "  file:file9.zip",
    "  file:file10.zip",
  ]);
  assert.equal(rows.find((r) => r.id === "Backups/Server A").parent, rows.find((r) => r.id === "Backups").key,
    "a row names the folder row it is in, where ← goes");
  assert.deepEqual(unread(rows, reads), [], "folders not made yet are never read");
});

test("a folder opens to Reading…, then its rows; a failed read says why; a cut listing says so", () => {
  const top = read(["a", "b"]);
  let rows = browserRows({ root: "bucket", reads: new Map([["", top]]), expanded: ["", "a"] });
  assert.deepEqual(shape(rows), ["folder:bucket", "  folder:a", "    note:Reading…", "  folder:b"]);
  assert.deepEqual(unread(rows, new Map([["", top]])), ["a"]);

  const failedReads = new Map([["", top], ["a", { loaded: false, error: "403 Forbidden" }]]);
  rows = browserRows({ root: "bucket", reads: failedReads, expanded: ["", "a"] });
  const failed = rows.find((r) => r.kind === "note");
  assert.equal(failed.name, "Could not read this folder: 403 Forbidden");
  assert.ok(failed.failed && failed.id === "a", "its Try again reads the folder again");
  assert.deepEqual(unread(rows, failedReads), [], "a failed read waits for Try again");

  rows = browserRows({ root: "bucket", reads: new Map([["", read(["a"], ["x"], true)]]), expanded: [""] });
  assert.equal(rows.at(-1).name, "Only the first 2 items are shown.");
});

test("the new folder's field sits after the folders of the one it is made in", () => {
  const rows = browserRows({ root: "My Drive", reads: new Map([["", read(["a"], ["f.zip"])]]), expanded: [""], naming: { parent: "" } });
  assert.deepEqual(shape(rows), ["folder:My Drive", "  folder:a", "  field:", "  file:f.zip"]);
  const empty = browserRows({ root: "My Drive", reads: new Map([["", read([])]]), expanded: [""], naming: { parent: "" } });
  assert.deepEqual(shape(empty), ["folder:My Drive", "  field:"], "a folder being named in is not empty");
});

test("paths and names", () => {
  assert.deepEqual(pathsTo(""), []);
  assert.deepEqual(pathsTo("a/b/c"), ["a", "a/b", "a/b/c"]);
  assert.equal(cleanName("  Server   A "), "Server A");
  assert.equal(nameProblem("Server A"), "");
  assert.ok(nameProblem("a/b"), "a / would make two folders");
});
