import test from "node:test";
import assert from "node:assert/strict";
import {
  approvalTitle, conversationId, declinedSummary, grantsIn, readGrants, waitsForPapers, withGrant,
  withoutGrants, withoutPaperWait, writeGrants,
} from "../src/chat/approvals.js";

test("the card's headline says what the call would do", () => {
  const card = (tool, preview = {}, args = {}) => approvalTitle({ tool, preview, args });
  assert.equal(card("rename_page", { title: "Attention" }), "Rename “Attention”");
  assert.equal(card("move_page", { title: "Attention", to: "ML/Vision" }), "Move “Attention” to ML/Vision");
  assert.equal(card("move_page", { title: "Attention", to: "" }), "Move “Attention” to the library root");
  assert.equal(card("edit_block", { title: "N", mode: "append" }), "Add to a note in “N”");
  assert.equal(card("edit_block", { title: "N", mode: "selection" }), "Edit your selection in “N”");
  assert.equal(card("edit_block", { title: "N", mode: "replace" }), "Edit a note in “N”");
  assert.equal(card("create_block", { title: "N", parent: "Methods" }), "Add a note under “Methods” in “N”");
  assert.equal(card("move_block", { title: "N", src_title: "M" }), "Move a note from “M” to “N”");
  assert.equal(card("save_paper", { title: "Attention", to: "refs" }), "Save “Attention” to refs");
  assert.equal(card("save_paper", { title: "arXiv:1706.03762", to: "" }), "Save “arXiv:1706.03762” to the library root");
  assert.equal(card("save_paper", { title: "Attention", to: "refs", existed: true }), "File “Attention” in refs");
  assert.equal(card("restore_page", { title: "Old draft", to: "ML" }), "Restore “Old draft” from Recently deleted");
  assert.equal(card("delete_block", { title: "N", what: "highlight", children: 2 }), "Delete a highlight in “N”");
  assert.equal(card("delete_block", { title: "N", what: "something new" }), "Delete a note in “N”");
  assert.equal(card("clip_region", { title: "N", what: "PDF page 2 (a region of it)" }), "Clip a picture of PDF page 2 (a region of it) in “N”");
  assert.equal(card("view_image", {}, { block_id: "b" }), "Look at a note's pictures");
  // A reading tool the user set to ask names its arguments, a page by title.
  assert.equal(approvalTitle({ tool: "read_page", args: { page_id: "a" } }, { titleOf: (id) => (id === "a" ? "Attention" : "") }),
    "Read “Attention”");
  assert.equal(card("fetch_paper", {}, { source: "doi:10.1/x" }), "Fetch doi:10.1/x");
  assert.equal(approvalTitle({ tool: "a_new_tool" }, { permission: "Do things" }), "Use “Do things”");
  // A declined call's chip, from the fields copied onto it.
  assert.equal(declinedSummary({ approval: "deny", tool: "edit_block", title: "N", mode: "append" }),
    "Not allowed by you: Add to a note in “N”");
  assert.equal(declinedSummary({ approval: "expired", tool: "move_page", title: "A", args: { folder: "ML" } }),
    "Not answered in time: Move “A” to ML");
});

test("allowing in a chat lasts for that conversation and account only", () => {
  const conversation = conversationId([{ role: "user" }, { id: "m1", role: "user" }, { id: "m2", role: "ai" }]);
  assert.equal(conversation, "m1");
  assert.equal(conversationId([]), "");
  let store = withGrant({}, "ada", conversation, "block_edit", 1);
  store = withGrant(store, "ada", conversation, "rename", 2);
  store = withGrant(store, "ada", conversation, "rename", 3);
  assert.deepEqual(grantsIn(store, "ada", conversation), ["block_edit", "rename"]);
  assert.deepEqual(grantsIn(store, "bo", conversation), [], "another account on this browser asks again");
  assert.deepEqual(grantsIn(store, "ada", "m9"), [], "another conversation asks again");
  assert.deepEqual(grantsIn(store, "ada", ""), [], "a conversation without an id has no grants");
  assert.deepEqual(grantsIn(withoutGrants(store, "ada", conversation), "ada", conversation), []);
  // Only the most recently used conversations are kept.
  let many = {};
  for (let i = 0; i < 120; i += 1) many = withGrant(many, "ada", `c${i}`, "move", i);
  assert.equal(Object.keys(many).length, 100);
  assert.deepEqual(grantsIn(many, "ada", "c0"), []);
  assert.deepEqual(grantsIn(many, "ada", "c119"), ["move"]);
});

test("grants survive a reload through storage, and bad storage asks again", () => {
  const box = new Map();
  const storage = { getItem: (k) => box.get(k) ?? null, setItem: (k, v) => box.set(k, v) };
  writeGrants(withGrant({}, "ada", "m1", "move", 1), storage);
  assert.deepEqual(grantsIn(readGrants(storage), "ada", "m1"), ["move"]);
  box.set("gamma-ai-chat-grants", "{not json");
  assert.deepEqual(readGrants(storage), {});
  box.set("gamma-ai-chat-grants", "[1, 2]");
  assert.deepEqual(readGrants(storage), {});
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("full"); } };
  assert.deepEqual(readGrants(broken), {});
  writeGrants({ x: 1 }, broken); // no throw
});

test("a conversation can stop waiting for blocked papers, without losing its grants", () => {
  let store = withGrant({}, "ada", "c1", "move", 1);
  assert.equal(waitsForPapers(store, "ada", "c1"), true);
  store = withoutPaperWait(store, "ada", "c1", 2);
  assert.equal(waitsForPapers(store, "ada", "c1"), false);
  assert.deepEqual(grantsIn(store, "ada", "c1"), ["move"], "the grant survives the second decision");
  // Another conversation, and another account, decide for themselves.
  assert.equal(waitsForPapers(store, "ada", "c2"), true);
  assert.equal(waitsForPapers(store, "bob", "c1"), true);
  // A chat with no first message yet waits, and records nothing.
  assert.equal(waitsForPapers(store, "ada", ""), true);
  assert.deepEqual(withoutPaperWait(store, "ada", "", 3), store);
});
