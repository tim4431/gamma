import assert from "node:assert/strict";
import { test } from "node:test";
import { canRevert, forReplay, markReverted, revertOrder, revertRefusal } from "../src/chat/aiRevert.js";
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
