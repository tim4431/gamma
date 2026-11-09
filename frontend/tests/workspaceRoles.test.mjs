// settings/workspaceRoles.js: the role a tab acts with, and the switcher's
// line, for a workspace being deleted (docs/dev/workspaces.md "Deleting a
// workspace"): it takes no more changes, so everyone acts as a viewer.
import assert from "node:assert/strict";
import { test } from "node:test";
import { actingRole, markDeleting, shareDeleting, workspaceMeta } from "../src/settings/workspaceRoles.js";

test("a workspace being deleted is acted on as a viewer, whatever the role", () => {
  assert.equal(actingRole({ role: "owner" }), "owner");
  assert.equal(actingRole({ role: "editor", deleting: false }), "editor");
  assert.equal(actingRole({ role: "owner", deleting: true }), "viewer");
  assert.equal(actingRole({ role: "viewer", deleting: true }), "viewer");
  assert.equal(actingRole(null), "");
});

test("a refusal marks only the workspace it names, and only once", () => {
  const lab = { id: "lab", role: "editor" }, home = { id: "home", role: "owner" };
  assert.deepEqual(markDeleting(lab, "lab"), { id: "lab", role: "editor", deleting: true });
  assert.equal(markDeleting(home, "lab"), home);
  const marked = markDeleting(lab, "lab");
  assert.equal(markDeleting(marked, "lab"), marked, "the same object: nothing re-renders");
  assert.equal(markDeleting(null, "lab"), null);
});

test("a share view of a workspace being deleted edits no more, on every page it opens next", () => {
  const info = { owner: "alice", role: "edit", canEdit: true };
  assert.deepEqual(shareDeleting(info), { owner: "alice", role: "edit", canEdit: false, deleting: true });
  const marked = shareDeleting(info);
  assert.equal(shareDeleting(marked), marked, "the same object: nothing re-renders");
  assert.equal(shareDeleting(null), null);
});

test("the switcher's line says a workspace is being deleted", () => {
  assert.equal(workspaceMeta({ personal: true, default: false, role: "owner" }), "personal");
  assert.equal(workspaceMeta({ personal: true, role: "owner", deleting: true }), "being deleted");
  assert.equal(workspaceMeta({ access: "private", role: "editor", deleting: true }), "being deleted");
});
