// The folder browser's rows (explorer/folderBrowser.js): a store's folders
// read a level at a time, the folders not made yet merged among the read
// ones by name, the new folder's field, a folder's files, and the note a
// folder says while it is read, after a failed read, empty or cut short;
// the folders to read next; and a new folder's name.
import assert from "node:assert/strict";
import { test } from "node:test";
import { browserRows, cleanName, nameProblem, pathsTo, unread } from "../src/explorer/folderBrowser.js";

const read = (folders, files = [], more = false) => ({ loaded: true, loading: false, error: "",
  folders: folders.map((name) => ({ name, modified: "" })), files, more });
const shape = (rows) => rows.map((r) => `${"  ".repeat(r.level - 1)}${r.kind}:${r.naming ? "(naming)" : r.name}${r.made ? "" : " (not made)"}`);

test("the top, its folders by name with the ones not made yet among them, then files and the notes", () => {
  const reads = new Map([
    ["", read(["Gamma backups", "Archive 10", "Archive 9"], [{ name: "notes.txt", size: 3, modified: "2026-11-20T10:00:00Z" }])],
    ["Gamma backups", read([], [], false)],
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
    "  file:notes.txt",
  ]);
  const backups = rows.find((r) => r.id === "Backups");
  assert.equal(rows.find((r) => r.id === "Backups/Server A").parent, backups.key, "a row names the folder row it is in");
  assert.equal(rows.find((r) => r.kind === "file").size, 3);
  assert.deepEqual(unread(rows, reads), [], "folders not made yet are never read");
});

test("a folder opens to Reading…, then its rows; a failed read says why; a cut listing says so", () => {
  const top = read(["a", "b"]);
  let rows = browserRows({ root: "bucket", reads: new Map([["", top]]), expanded: ["", "a"] });
  assert.deepEqual(shape(rows), ["folder:bucket", "  folder:a", "    note:Reading…", "  folder:b"]);
  assert.equal(rows[2].loading, true);
  assert.deepEqual(unread(rows, new Map([["", top]])), ["a"]);

  rows = browserRows({ root: "bucket", reads: new Map([["", top], ["a", { loaded: false, error: "403 Forbidden" }]]), expanded: ["", "a"] });
  const failed = rows.find((r) => r.kind === "note");
  assert.equal(failed.name, "Could not read this folder: 403 Forbidden");
  assert.ok(failed.failed && failed.id === "a", "its Try again reads the folder again");
  assert.deepEqual(unread(rows, new Map([["", top], ["a", {}]])), [], "a failed read waits for Try again");

  rows = browserRows({ root: "bucket", reads: new Map([["", read(["a"], [{ name: "x" }], true)]]), expanded: [""] });
  assert.equal(rows.at(-1).name, "Only the first 2 items are shown.");
});

test("the new folder's field sits after the folders of the one it is made in", () => {
  const rows = browserRows({ root: "My Drive", reads: new Map([["", read(["a"], [{ name: "f.zip" }])]]), expanded: [""],
    naming: { parent: "" } });
  assert.deepEqual(shape(rows), ["folder:My Drive", "  folder:a", "  folder:(naming)", "  file:f.zip"]);
  const empty = browserRows({ root: "My Drive", reads: new Map([["", read([])]]), expanded: [""], naming: { parent: "" } });
  assert.deepEqual(shape(empty), ["folder:My Drive", "  folder:(naming)"], "a folder being named in is not empty");
});

test("paths and names", () => {
  assert.deepEqual(pathsTo(""), []);
  assert.deepEqual(pathsTo("a/b/c"), ["a", "a/b", "a/b/c"]);
  assert.equal(cleanName("  Server   A "), "Server A");
  assert.equal(nameProblem("Server A"), "");
  for (const bad of ["..", "a/b", "a\\b", "x".repeat(121), "tab\u0001"]) assert.ok(nameProblem(bad), bad);
});
