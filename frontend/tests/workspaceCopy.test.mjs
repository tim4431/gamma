import test from "node:test";
import assert from "node:assert/strict";
import { copyBody, copySummary, copyTargets, duplicateName, zoteroPdfsNote } from "../src/transfers/workspaceCopy.js";
import { retryOf, taskTitle } from "../src/tasks/taskKinds.js";

const ws = (id, role, more = {}) => ({ id, name: id.toUpperCase(), role, personal: true, ...more });

test("pages are copied into the workspaces the account edits, never the one they are in", () => {
  const list = [ws("mine", "owner"), ws("here", "owner"), ws("lab", "editor", { personal: false }),
    ws("read", "viewer", { personal: false }), ws("going", "owner", { deleting: true })];
  assert.deepEqual(copyTargets(list, "here").map((w) => w.id), ["mine", "lab"]);
  assert.deepEqual(copyTargets(null, "here"), []);
});

test("the job's body names the pages and one target: a workspace, or a new one's name", () => {
  assert.deepEqual(copyBody({ ws: "a", pages: ["p1", "p2"], to: "b" }), { ws: "a", pages: ["p1", "p2"], to: "b" });
  assert.deepEqual(copyBody({ ws: "a", name: "A (copy)" }), { ws: "a", name: "A (copy)" }); // every page
  assert.equal(duplicateName("Reading"), "Reading (copy)");
});

test("what a copy says when it is done, and of the PDFs still in Zotero", () => {
  assert.equal(copySummary({ pages_added: 3, pages_skipped: 0, zotero_pdfs: 0 }, { to_name: "Lab" }), "Copied 3 pages to “Lab”.");
  assert.equal(copySummary({ pages_added: 1, pages_skipped: 2, zotero_pdfs: 1 }, { to_name: "Lab" }),
    "Copied 1 page to “Lab”. 2 were there already. 1 came without its PDF, which is still in Zotero.");
  assert.equal(copySummary({ pages_added: 40 }, { new: true, name: "Zotero", to_name: "Zotero (copy)" }),
    "Duplicated “Zotero” as “Zotero (copy)”.");
  assert.match(zoteroPdfsNote(2), /^2 papers' PDFs are still in Zotero/);
  assert.match(zoteroPdfsNote(1), /^1 paper's PDF is still in Zotero/);
});

test("a copy's row: its title, and a start again only for a copy of every page", () => {
  const copy = { kind: "workspace-copy", state: "failed", params: { ws: "a", name: "A", to: "b", to_name: "Lab", pages: 2, all: false } };
  assert.equal(taskTitle(copy), "Copy 2 pages to “Lab”");
  assert.equal(retryOf(copy), null); // the picked pages are picked again in the library
  const dup = { kind: "workspace-copy", state: "failed", params: { ws: "a", name: "A", to: "", to_name: "A (copy)", pages: 9, all: true, new: true } };
  assert.equal(taskTitle(dup), "Duplicate “A” as “A (copy)”");
  assert.deepEqual(retryOf(dup), ["workspace-copy", { ws: "a", name: "A (copy)" }]);
  assert.deepEqual(retryOf({ ...dup, params: { ...dup.params, new: false, to: "b" } }), ["workspace-copy", { ws: "a", to: "b" }]);
});
