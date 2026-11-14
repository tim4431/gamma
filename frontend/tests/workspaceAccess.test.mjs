// settings/workspaceAccess.js: a connection's levels per workspace (View,
// Comment, Edit) as the picker holds them and the API takes them
// (docs/dev/mcp.md "The Comment level", "Editing notes").
import assert from "node:assert/strict";
import { test } from "node:test";
import { accessBody, accessOf, comments, edits, levelOf } from "../src/settings/workspaceAccess.js";

test("each ticked workspace is at one level, and the body sends those it reads", () => {
  const access = { all: false, ids: ["a", "b", "c"], comments: ["b", "gone"], edits: ["c"] };
  assert.deepEqual(["a", "b", "c"].map((id) => levelOf(access, id)), ["view", "comment", "edit"]);
  assert.deepEqual(accessBody(access), { all_workspaces: false, workspaces: ["a", "b", "c"], comment_all: false,
    comment_workspaces: ["b"], edit_all: false, edit_workspaces: ["c"] });
  assert.deepEqual(accessBody({ all: true, ids: ["a"], commentAll: true, comments: ["a"], editAll: false }), {
    all_workspaces: true, workspaces: [], comment_all: true, comment_workspaces: [], edit_all: false, edit_workspaces: [] });
});

test("the Edit level comments too", () => {
  assert.equal(comments({ all: false, ids: ["a"], comments: [], edits: ["a"] }), true);
  assert.equal(comments({ all: false, ids: ["a"], comments: ["b"], edits: [] }), false, "only where it reads");
  assert.equal(comments({ all: true, ids: [], commentAll: false, editAll: true }), true);
  assert.equal(edits({ all: false, ids: ["a"], comments: ["a"], edits: [] }), false);
});

test("a workspace the account now only views holds its stored Edit as Comment, and a save sends that", () => {
  const item = { all_workspaces: false, workspaces: ["lab", "own"], comment_all: false, comment_workspaces: [],
    edit_all: false, edit_workspaces: ["lab", "own"] };
  const access = accessOf(item, [{ id: "lab", role: "viewer" }, { id: "own", role: "owner" }]);
  assert.deepEqual([levelOf(access, "lab"), levelOf(access, "own")], ["comment", "edit"]);
  const body = accessBody(access);
  assert.deepEqual([body.comment_workspaces, body.edit_workspaces], [["lab"], ["own"]]);
  assert.deepEqual(accessOf(item).edits, ["lab", "own"], "without the roles, as stored");
});
