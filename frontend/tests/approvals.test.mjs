import test from "node:test";
import assert from "node:assert/strict";
import {
  approvalTitle, batchNotes, conversationId, declinedSummary, deletionNotes, grantsIn, readGrants, waitsForPapers,
  withGrant, withoutGrants, withoutPaperWait, writeGrants,
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

test("folder and label cards say what goes", () => {
  const card = (tool, preview = {}) => approvalTitle({ tool, preview });
  assert.equal(card("rename_folder", { title: "Physics / QEC" }), "Rename the folder “Physics / QEC”");
  assert.equal(card("rename_label", { title: "to-read" }), "Rename the label “to-read”");
  assert.equal(card("create_label", { title: "Qubits" }), "Make the label “Qubits”");
  assert.equal(card("label_page", { title: "Attention" }), "Change the labels of “Attention”");
  assert.equal(card("delete_folder", { title: "Physics / QEC" }), "Delete the folder “Physics / QEC”");
  assert.equal(card("delete_label", { title: "ML" }), "Delete the label “ML”");
  // A deletion names what it takes besides itself; the rest are counted.
  assert.deepEqual(deletionNotes("delete_folder", { subfolders: ["A / B"], subfolder_count: 3, pages: ["P"], page_count: 1,
    chats: 2, chat_folders: ["A / B"], chat_folder_count: 1 }), [
    "Its 3 subfolders go with it: A / B, and 2 more",
    "1 page leaves it and stays in your library: P",
    "2 AI chats of A / B move to the library chat's history and stay there if you revert.",
  ]);
  assert.deepEqual(deletionNotes("delete_folder", { page_count: 0 }), ["No pages are filed in it."]);
  assert.deepEqual(deletionNotes("delete_label", { pages: ["P", "Q"], page_count: 2 }),
    ["The label comes off 2 pages; no page is deleted: P, Q"]);
  assert.deepEqual(deletionNotes("delete_label", { page_count: 0 }), ["No page carries it."]);
  assert.deepEqual(deletionNotes("rename_folder", {}), []);
  assert.equal(declinedSummary({ approval: "deny", tool: "delete_label", title: "ML" }), "Not allowed by you: Delete the label “ML”");
});

test("a card for several pages counts them and notes what it leaves out", () => {
  const card = (tool, preview = {}) => approvalTitle({ tool, preview });
  assert.equal(card("rename_page", { count: 37 }), "Rename 37 pages");
  assert.equal(card("rename_page", { count: 1 }), "Rename 1 page");
  assert.equal(card("move_page", { count: 2, to: "QEC" }), "Move 2 pages to QEC");
  assert.equal(card("move_page", { count: 2, to: "" }), "Move 2 pages to the library root");
  assert.equal(card("label_page", { count: 4 }), "Change the labels of 4 pages");
  assert.equal(card("restore_page", { count: 3 }), "Restore 3 pages from Recently deleted");
  // A one-page card carries no count and keeps its own headline.
  assert.equal(card("rename_page", { title: "A" }), "Rename “A”");
  assert.equal(card("restore_page", { title: "A", to: "" }), "Restore “A” from Recently deleted");
  // Another tool's count is not a page count.
  assert.equal(card("delete_label", { title: "ML", count: 3 }), "Delete the label “ML”");
  assert.deepEqual(batchNotes({ to: "A / B", new_folder: true, left_out: ["“X”", "id9"], left_out_count: 3, same_count: 2 }), [
    "The folder A / B does not exist yet: it is made first.",
    "3 pages are left out: “X”, id9, and 1 more",
    "2 pages already are so and stay as they are.",
  ]);
  assert.deepEqual(batchNotes({ new_labels: ["n1"] }), ["A new label is made first: n1"]);
  assert.deepEqual(batchNotes({ new_labels: ["n1", "n2"], left_out: ["“X”"], left_out_count: 1, same_count: 1 }), [
    "2 new labels are made first: n1, n2",
    "1 page is left out: “X”",
    "1 page already is so and stays as it is.",
  ]);
  assert.deepEqual(batchNotes({ count: 2, diff: [] }), []);
  assert.deepEqual(batchNotes(), []);
  // A declined batch's chip counts its pages.
  assert.equal(declinedSummary({ approval: "deny", tool: "move_page", count: 3, args: { folder: "QEC" } }),
    "Not allowed by you: Move 3 pages to QEC");
  assert.equal(declinedSummary({ approval: "expired", tool: "rename_page", count: 37 }), "Not answered in time: Rename 37 pages");
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
