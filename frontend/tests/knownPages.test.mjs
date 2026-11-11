// The pages the client knows (explorer/knownPages.js): the listing's rows
// and those read beside it as one list, and which of them the library
// lists, a sub-page by its top.
import assert from "node:assert/strict";
import { test } from "node:test";
import { listedOf, mergeKnown } from "../src/explorer/knownPages.js";

const row = (id, properties = {}, parent = "") => ({ id, content: id, properties, _parentPage: parent });
const file = (id, more = {}) => row(id, { work_folder: "W1", file_name: `${id}.pdf`, folders: ["D1"], ...more });

test("one list by id: the listing's rows in their order, then the extra rows it lacks", () => {
  const listing = [row("a"), file("pinned", { pinned: "2026-11-10T08:00:00Z" })];
  const extra = [{ id: "a", content: "stale", properties: {} }, { id: "f1", content: "f1", properties: {}, parent: "" },
    { id: "sub", content: "sub", properties: {}, parent: "f1" }, { id: "f1", content: "again", properties: {} }];
  const merged = mergeKnown(listing, extra);
  assert.deepEqual(merged.map((r) => r.id), ["a", "pinned", "f1", "sub"]);
  assert.equal(merged[0], listing[0], "the listing's row wins");
  assert.equal(merged[2].content, "f1", "an extra row read twice: the first");
  assert.deepEqual(merged.map((r) => r._parentPage), ["", "", "", "f1"], "an extra row's parent, as App gives the listing's");
  assert.equal(mergeKnown(listing, []), listing, "nothing beside: the listing itself");
  assert.equal(mergeKnown(listing, undefined), listing);
  const kept = { id: "k", properties: {}, _parentPage: "a", parent: "" };
  assert.equal(mergeKnown(listing, [kept]).at(-1), kept, "a row that has its _parentPage keeps it");
});

test("the library lists a library page and a file in the library, a sub-page by its top", () => {
  const rows = [row("lib"), file("f"), file("added", { library: true }), row("inLib", {}, "lib"), row("inFile", {}, "f"),
    row("deeper", {}, "inFile"), row("lost", {}, "gone"), row("x", {}, "y"), row("y", {}, "x")];
  const byId = new Map(rows.map((r) => [r.id, r]));
  const listed = (id) => listedOf(byId.get(id), byId);
  assert.equal(listed("lib"), true);
  assert.equal(listed("f"), false, "a work folder's file, not in the library");
  assert.equal(listed("added"), true);
  assert.equal(listed("inLib"), true);
  assert.equal(listed("inFile"), false, "a page under a file goes by the file");
  assert.equal(listed("deeper"), false, "at any depth");
  assert.equal(listed("lost"), true, "a top the client does not know counts as listed");
  assert.equal(listed("x"), true, "a loop stops");
});
