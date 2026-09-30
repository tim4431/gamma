import assert from "node:assert/strict";
import { test } from "node:test";
import { changePlace, isChange, runningLabel, splitActions, stepsSummary } from "../src/chat/agentSteps.js";

const actions = [
  { kind: "list", tool: "list_pages", summary: "Listed 12 pages" },
  { kind: "read", tool: "read_page", summary: "Read “A”", page_id: "a" },
  { kind: "rename", tool: "rename_page", summary: "Renamed “A” → “B”", page_id: "a", from: "A", to: "B", title: "A" },
  { kind: "move", tool: "move_page", summary: "Moved “C” → ML", page_id: "c", from: "", to: "ML", title: "C" },
  { kind: "move", tool: "move_page", summary: "ok — page is already there", noop: true },
  { kind: "edit", tool: "edit_block", summary: "Appended to a note in “N”", page_id: "n", block_id: "b1", mode: "append", title: "N" },
  { kind: "error", tool: "rename_page", summary: "error: no such page", error: true },
];

test("the pill sums every step and names the reading ones", () => {
  assert.equal(stepsSummary(actions), "7 steps · listed, read 1 page");
  assert.equal(stepsSummary([actions[1], actions[1]]), "2 steps · read 2 pages");
  assert.equal(stepsSummary([]), "0 steps");
});

test("changes are split by where they landed; failures and no-ops are not changes", () => {
  const { library, notes, failed } = splitActions(actions);
  assert.deepEqual(library.map((a) => a.to), ["B", "ML"]);
  assert.deepEqual(notes.map((a) => a.block_id), ["b1"]);
  assert.equal(failed, 1);
  // Chats saved before `tool`, `noop` and the structured fields.
  assert.equal(changePlace({ kind: "move", block_id: "x" }), "notes");
  assert.equal(changePlace({ kind: "rename" }), "library");
  assert.equal(isChange({ kind: "rename", summary: "ok — title already is that" }), false);
  // A call the user did not allow is neither a change nor a failure.
  const declined = { kind: "error", tool: "rename_page", summary: "Not allowed: rename_page", error: true, declined: true };
  assert.deepEqual(splitActions([...actions, declined]).declined, 1);
  assert.equal(splitActions([...actions, declined]).failed, 1);
  assert.equal(isChange(declined), false);
});

test("saved and restored pages are library changes; handwriting and citations are reading steps", () => {
  const more = [
    { kind: "ink", tool: "view_ink", summary: "Looked at handwriting in “N”", page_id: "n", block_id: "i1" },
    { kind: "ink", tool: "view_ink", summary: "Looked at handwriting in “N”", page_id: "n", block_id: "i2" },
    { kind: "cite", tool: "cite", summary: "Cited 3 pages" },
    { kind: "save", tool: "save_paper", summary: "Saved “P” to ML", page_id: "p", title: "P", to: "ML" },
    { kind: "restore", tool: "restore_page", summary: "Restored “Q”", page_id: "q", title: "Q", to: "" },
    { kind: "save", tool: "save_paper", summary: "ok — [P](/?page=p) is already in the library; nothing changed", noop: true },
  ];
  assert.equal(stepsSummary(more), "6 steps · looked at handwriting 2 times, cited");
  const { library, notes } = splitActions(more);
  assert.deepEqual(library.map((a) => a.page_id), ["p", "q"]);
  assert.deepEqual(notes, []);
});

test("the running step reads as what the agent is doing", () => {
  const titleOf = (id) => (id === "a" ? "Attention" : "");
  assert.equal(runningLabel({ tool: "search_library", args: { query: "scaled dot-product" } }), "Searching library for “scaled dot-product”…");
  assert.equal(runningLabel({ tool: "read_page", args: { page_id: "a" } }, titleOf), "Reading “Attention”…");
  assert.equal(runningLabel({ tool: "read_page", args: { page_id: "zz" } }, titleOf), "Reading a page…");
  assert.equal(runningLabel({ tool: "something_new", args: {} }), "Working…");
  assert.equal(runningLabel({ tool: "search_web", args: { query: "raman lab" } }), "Searching the web for “raman lab”…");
  assert.equal(runningLabel({ tool: "related_papers", args: { source: "doi:10.1/x" } }), "Following citations of doi:10.1/x…");
  assert.equal(runningLabel({ tool: "related_papers", args: {} }), "Following citations of a paper…");
  // The arguments a change is about: the new title, the folder, the edit's mode.
  assert.equal(runningLabel({ tool: "rename_page", args: { page_id: "a", title: "Ada2019" } }, titleOf), "Renaming “Attention” to “Ada2019”…");
  assert.equal(runningLabel({ tool: "rename_page", args: { page_id: "zz", title: "Ada2019" } }, titleOf), "Renaming to “Ada2019”…");
  assert.equal(runningLabel({ tool: "move_page", args: { page_id: "a", folder: "ML/Generative" } }, titleOf), "Moving “Attention” to ML/Generative…");
  assert.equal(runningLabel({ tool: "move_page", args: { page_id: "a", folder: "" } }, titleOf), "Moving “Attention”…");
  assert.equal(runningLabel({ tool: "edit_block", args: { mode: "append" } }), "Appending to a note…");
  assert.equal(runningLabel({ tool: "edit_block", args: {} }), "Editing a note…");
  // read_block names the page when its block id is one; list_pages its filter.
  assert.equal(runningLabel({ tool: "read_block", args: { block_id: "a" } }, titleOf), "Reading notes of “Attention”…");
  assert.equal(runningLabel({ tool: "read_block", args: { block_id: "b7" } }, titleOf), "Reading notes…");
  assert.equal(runningLabel({ tool: "list_pages", args: { folder: "ML" } }), "Listing pages in ML…");
  assert.equal(runningLabel({ tool: "list_pages", args: { label: "to-read", folder: "ML" } }), "Listing pages labelled “to-read”…");
  assert.equal(runningLabel({ tool: "view_ink", args: { block_id: "i1" } }), "Looking at handwriting…");
  assert.equal(runningLabel({ tool: "save_paper", args: { source: "arXiv:2601.1", title: "Attention" } }), "Saving Attention to your library…");
  assert.equal(runningLabel({ tool: "save_paper", args: { source: "arXiv:2601.1" } }), "Saving arXiv:2601.1 to your library…");
  assert.equal(runningLabel({ tool: "restore_page", args: { page_id: "a" } }, titleOf), "Restoring “Attention”…");
  assert.equal(runningLabel({ tool: "list_deleted", args: {} }), "Looking in Recently deleted…");
});
