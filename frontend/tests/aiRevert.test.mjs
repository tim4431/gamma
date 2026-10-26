import assert from "node:assert/strict";
import { test } from "node:test";
import { canRedo, canRevert, forReplay, markReverted, revertKind, revertOrder, revertRefusal } from "../src/chat/aiRevert.js";
import { createChatSession } from "../src/chat/chatSession.js";

const edit = (id, extra = {}) => ({ kind: "edit", tool: "edit_block", block_id: id, page_id: "p",
  summary: "Edited a note", revert: { before: "a", after: "b" }, ...extra });

test("only the agent's recorded note changes can be reverted, once", () => {
  assert.equal(canRevert(edit("b1")), true);
  assert.equal(canRevert({ ...edit("b1"), kind: "create", tool: "create_block" }), true);
  assert.equal(canRevert({ ...edit("b1"), kind: "move", tool: "move_block" }), true);
  assert.equal(canRevert(edit("b1", { reverted: true })), false);
  assert.equal(canRevert(edit("b1", { revert: undefined })), false); // saved before reverting existed
  assert.equal(canRevert(edit("b1", { noop: true })), false); // changed nothing
  assert.equal(canRevert(edit("b1", { error: true })), false);
  assert.equal(canRevert({ kind: "rename", tool: "rename_page", page_id: "p", summary: "Renamed", revert: {} }), false);
});

test("a deleted folder or label reverts from its library row, outside Revert all", () => {
  const folderDelete = { kind: "delete", tool: "delete_folder", block_id: "f", title: "X", count: 1,
    summary: "Deleted the folder “X”", revert: { tree: "folders", blocks: [], pages: {} } };
  assert.equal(canRevert(folderDelete), true);
  assert.equal(canRedo({ ...folderDelete, reverted: true }), true);
  assert.equal(canRevert({ ...folderDelete, tool: "delete_label", summary: "Deleted the label “X”" }), true);
  // A rename is undone by another call, whatever it carries.
  assert.equal(canRevert({ kind: "rename", tool: "rename_folder", block_id: "f", summary: "Renamed the folder “X” → “Y”",
    from: "X", to: "Y", revert: { tree: "folders" } }), false);
  assert.deepEqual(revertOrder([edit("b1"), folderDelete]), [0]);
});

test("a page tool's change reverts from its library row, one page or many, without a block id", () => {
  const renamed = { kind: "rename", tool: "rename_page", summary: "Renamed 2 pages", changes: 2,
    items: [{ page_id: "a", title: "x", from: "x", to: "y" }, { page_id: "b", title: "u", from: "u", to: "v" }],
    revert: { pages: [["a", "title", "x", "y"], ["b", "title", "u", "v"]] } };
  assert.equal(canRevert(renamed), true);
  assert.equal(canRedo(renamed), false);
  assert.equal(canRedo({ ...renamed, reverted: true }), true);
  assert.equal(canRevert({ ...renamed, reverted: true }), false);
  // One page's move records the same; an empty or missing list is nothing to revert.
  const moved = { kind: "move", tool: "move_page", page_id: "a", title: "A", from: "", to: "QEC", summary: "Moved “A” → QEC",
    revert: { pages: [["a", "folders", [], ["f1"]]] } };
  assert.equal(canRevert(moved), true);
  assert.equal(canRevert({ ...moved, revert: { pages: [] } }), false);
  assert.equal(canRevert({ ...moved, revert: { pages: {} } }), false);
  // A restore records none.
  assert.equal(canRevert({ kind: "restore", tool: "restore_page", summary: "Restored 2 pages", changes: 2, items: [] }), false);
  assert.equal(canRevert({ kind: "rename", tool: "rename_folder", block_id: "f", summary: "Renamed the folder “X” → “Y”",
    revert: { pages: [["a", "title", "x", "y"]] } }), false);
  // The endpoint's kind: "pages" for a page tool, the action's own otherwise.
  assert.equal(revertKind(renamed), "pages");
  assert.equal(revertKind(moved), "pages");
  assert.equal(revertKind(edit("b1")), "edit");
  assert.equal(revertKind({ kind: "delete", tool: "delete_folder", block_id: "f", revert: { tree: "folders", pages: { a: ["f"] } } }),
    "delete");
  // Revert all is the notes' only.
  assert.deepEqual(revertOrder([edit("b1"), renamed]), [0]);
});

test("a revert or redo that left pages as they were keeps how many; a clean one drops the count", () => {
  const renamed = { kind: "rename", tool: "rename_page", summary: "Renamed 3 pages", revert: { pages: [["a", "title", "x", "y"]] } };
  const messages = [{ id: "r1", role: "ai", text: "Done", actions: [renamed] }];
  const reverted = markReverted(messages, "r1", [0], true, { kept: 2 });
  assert.equal(reverted[0].actions[0].reverted, true);
  assert.equal(reverted[0].actions[0].kept, 2);
  const redone = markReverted(reverted, "r1", [0], false);
  assert.equal("reverted" in redone[0].actions[0], false);
  assert.equal("kept" in redone[0].actions[0], false);
  // A redo that left pages keeps its own count, unmarked; the next revert
  // replaces it, or drops it when it left none.
  const redoneKept = markReverted(reverted, "r1", [0], false, { kept: 1 });
  assert.equal("reverted" in redoneKept[0].actions[0], false);
  assert.equal(redoneKept[0].actions[0].kept, 1);
  assert.equal(markReverted(redoneKept, "r1", [0], true, { kept: 3 })[0].actions[0].kept, 3);
  assert.equal("kept" in markReverted(redoneKept, "r1", [0], true)[0].actions[0], false);
  // Without fields a revert saves the mark alone.
  assert.equal("kept" in markReverted(messages, "r1", [0])[0].actions[0], false);
  // Every page changed since: a 409 the row shows, and cannot force.
  const kept = Object.assign(new Error("The pages changed since, or were deleted, and were left as they are."), {
    status: 409, data: { detail: "…", conflict: "kept", preview: { kept: 3 } } });
  assert.deepEqual(revertRefusal(kept), { detail: "The pages changed since, or were deleted, and were left as they are.",
    status: 409, conflict: "kept", preview: { kept: 3 } });
  // The replay leaves out the per-page lists and keeps the counts.
  const [sent] = forReplay([{ ...renamed, changes: 3, kept: 1, reverted: true, items: [{ page_id: "a" }] }]);
  assert.deepEqual(sent, { kind: "rename", tool: "rename_page", summary: "Renamed 3 pages", changes: 3, kept: 1, reverted: true });
});

test("the replay leaves the reverting texts out and keeps the mark", () => {
  const sent = forReplay([edit("b1", { reverted: true }), { kind: "read", tool: "read_block" }]);
  assert.deepEqual(sent[0], { kind: "edit", tool: "edit_block", block_id: "b1", page_id: "p",
    summary: "Edited a note", reverted: true });
  assert.deepEqual(sent[1], { kind: "read", tool: "read_block" });
});

test("revert all goes newest first and skips what can't be reverted", () => {
  const actions = [edit("b1"), { kind: "read", tool: "read_block" }, edit("b2", { reverted: true }), edit("b3")];
  assert.deepEqual(revertOrder(actions), [3, 0]);
});

test("a revert marks its actions on its own message only", () => {
  const messages = [
    { id: "q", role: "user", text: "fix" },
    { id: "r1", role: "ai", text: "Done", actions: [edit("b1"), edit("b2")] },
    { id: "r2", role: "ai", text: "Again", actions: [edit("b1")] },
  ];
  const next = markReverted(messages, "r1", [1]);
  assert.equal(next[1].actions[1].reverted, true);
  assert.equal(next[1].actions[0].reverted, undefined);
  assert.equal(next[2], messages[2]);
  assert.equal(markReverted(next, "r1", [1]), next); // marked already: the same list
  assert.equal(markReverted(messages, "nope", [0]), messages);
  // A redo unmarks it, leaving no `reverted` key behind.
  const redone = markReverted(next, "r1", [1], false);
  assert.equal("reverted" in redone[1].actions[1], false);
  assert.equal(markReverted(redone, "r1", [1], false), redone);
});

test("only a reverted change can be redone, and a new note only with its place", () => {
  assert.equal(canRedo(edit("b1")), false);
  assert.equal(canRedo(edit("b1", { reverted: true })), true);
  assert.equal(canRedo(edit("b1", { reverted: true, revert: undefined })), false);
  const created = { ...edit("b1", { reverted: true }), kind: "create", tool: "create_block" };
  assert.equal(canRedo({ ...created, revert: { after: "x", parent: "p", position: "a0" } }), true);
  assert.equal(canRedo({ ...created, revert: { after: "x" } }), false); // recorded before redo existed
  assert.equal(canRedo({ ...edit("b1", { reverted: true }), kind: "move", tool: "move_block" }), true);
});

test("the refusal the row shows", () => {
  const conflict = Object.assign(new Error("The note was changed since."), {
    status: 409, data: { detail: "The note was changed since.", conflict: "changed", preview: { diff: [["del", "b"]] } } });
  assert.deepEqual(revertRefusal(conflict), { detail: "The note was changed since.", status: 409, conflict: "changed",
    preview: { diff: [["del", "b"]] } });
  const deleted = Object.assign(new Error("The note was deleted since."), { status: 404, data: { conflict: "gone" } });
  assert.deepEqual(revertRefusal(deleted), { detail: "The note was deleted since.", status: 404, conflict: "", preview: null });
  assert.deepEqual(revertRefusal(new Error("offline")), { detail: "offline", status: 0, conflict: "", preview: null });
});

test("an edit outside a reply is shown and saved against the version read", async () => {
  const saved = [];
  const session = createChatSession(async (key, messages, at) => { saved.push({ key, messages, at }); return { updated_at: "v2" }; });
  const stored = [{ id: "r1", role: "ai", text: "Done", actions: [edit("b1")] }];
  session.seen("page", stored, "v1");
  const next = markReverted(stored, "r1", [0]);
  await session.edit("page", next, "Title");
  assert.equal(session.getSnapshot().replies.get("page").messages, next);
  assert.equal(session.getSnapshot().replies.get("page").title, "Title");
  assert.deepEqual(saved, [{ key: "page", messages: next, at: "v1" }]);
  assert.equal(session.isSaved("page"), true);
  assert.equal(session.version("page"), "v2");
});
