// The AI's note changes a note shows as tracked changes (chat/noteReview.js):
// which actions become one (an edit that ran without asking), how a block's
// edits in a row chain, what the bar's Hide, Hide all and Revert do, and an
// edit waiting on its approval card, answered with the card's answers.
import assert from "node:assert/strict";
import { test } from "node:test";
import { changeSteps, noteChange, noteReview, withChange } from "../src/chat/noteReview.js";

const edit = (id, before, after, extra = {}) => ({ kind: "edit", tool: "edit_block", block_id: id, page_id: "p",
  summary: "Edited a note", revert: { before, after }, ...extra });
const create = (id, after) => ({ kind: "create", tool: "create_block", block_id: id, page_id: "p",
  summary: "Added a note", revert: { after, parent: "p", position: "a0" } });
const step = (action, index = 0) => ({ action, key: "conv", reply: "r1", index });

test("an edit or a new note that changed its text is a change a note can show", () => {
  assert.deepEqual(noteChange(edit("b", "old", "new")), { blockId: "b", pageId: "p", before: "old", after: "new" });
  assert.deepEqual(noteChange(create("n", "fresh")), { blockId: "n", pageId: "p", before: "", after: "fresh" });
  for (const a of [
    edit("b", "same", "same"), edit("b", "a", "b", { error: true }), edit("b", "a", "b", { noop: true }),
    edit("b", "a", "b", { reverted: true }), edit("b", "a", "b", { revert: undefined }),
    { kind: "move", tool: "move_block", block_id: "b", revert: { parent: "x" } },
    { kind: "read", tool: "read_block", block_id: "b" },
  ]) assert.equal(noteChange(a), null, JSON.stringify(a));
});

test("edits of one block in a row are one change; another start is a change of its own", () => {
  let changes = withChange(new Map(), step(edit("b", "one", "two"), 0));
  changes = withChange(changes, step(edit("b", "two", "three"), 1));
  const c = changes.get("b");
  assert.deepEqual([c.before, c.after, c.steps.map((s) => s.index)], ["one", "three", [0, 1]]);
  // Back where it started: nothing to show.
  assert.equal(withChange(changes, step(edit("b", "three", "one"), 2)).has("b"), false);
  // The block was changed in between (someone typed): the new edit stands alone.
  const fresh = withChange(changes, step(edit("b", "typed", "four"), 3)).get("b");
  assert.deepEqual([fresh.before, fresh.after, fresh.steps.length], ["typed", "four", 1]);
  // An edit on top of a new note keeps the note new.
  const made = withChange(withChange(new Map(), step(create("n", "draft"))), step(edit("n", "draft", "final"), 1));
  assert.deepEqual([made.get("n").before, made.get("n").after], ["", "final"]);
  assert.equal(withChange(changes, step({ kind: "read", tool: "read_block" })), changes, "nothing to add: the same map");
});

test("an edit that ran without asking shows until hidden; one allowed on its card was seen there", () => {
  noteReview.hidePage("p");
  noteReview.landed(step(edit("x", "a", "b", { approval: "once" })));
  assert.equal(noteReview.getSnapshot().changes.has("x"), false);
  noteReview.landed(step(edit("x", "a", "b")));
  noteReview.landed(step(edit("y", "c", "d"), 1));
  noteReview.landed({ ...step(edit("z", "e", "f"), 2), action: { ...edit("z", "e", "f"), page_id: "other" } });
  assert.deepEqual([...noteReview.getSnapshot().changes.keys()], ["x", "y", "z"]);
  // Each allowed answer on the card is seen there: once, for the chat, always.
  for (const approval of ["once", "chat", "always"]) noteReview.landed(step(edit("w", "g", "h", { approval })));
  assert.equal(noteReview.getSnapshot().changes.has("w"), false);
  noteReview.hide("x");
  assert.deepEqual([...noteReview.getSnapshot().changes.keys()], ["y", "z"]);
  noteReview.hidePage("p");
  assert.deepEqual([...noteReview.getSnapshot().changes.keys()], ["z"], "Hide all hides this page's alone");
  noteReview.hidePage("other");
});

test("Revert takes a change's steps back newest first, and a refusal says why", async () => {
  const asked = [];
  let refuse = -1;
  noteReview.connect(async (s, { force }) => {
    asked.push([s.index, force]);
    return s.index === refuse ? { text: "It was typed in since.", force: "Delete anyway" } : null;
  });
  noteReview.landed(step(edit("b", "one", "two"), 0));
  noteReview.landed(step(edit("b", "two", "three"), 1));
  assert.equal(await noteReview.revert("b"), null);
  assert.deepEqual(asked, [[1, false], [0, false]]);
  assert.equal(noteReview.getSnapshot().changes.has("b"), false, "reverted: no longer shown");

  // The first step back refused: nothing changed, the change still shows.
  asked.length = 0;
  refuse = 1;
  noteReview.landed(step(edit("b", "one", "two"), 0));
  noteReview.landed(step(edit("b", "two", "three"), 1));
  assert.deepEqual(await noteReview.revert("b"), { text: "It was typed in since.", force: "Delete anyway" });
  assert.equal(noteReview.getSnapshot().changes.has("b"), true);
  // Forced, it goes on.
  refuse = -1;
  asked.length = 0;
  assert.equal(await noteReview.revert("b", { force: true }), null);
  assert.deepEqual(asked, [[1, true], [0, true]]);

  // A later step refused: the change is partly back, and the chat's rows say the rest.
  refuse = 0;
  noteReview.landed(step(edit("b", "one", "two"), 0));
  noteReview.landed(step(edit("b", "two", "three"), 1));
  assert.ok(await noteReview.revert("b"));
  assert.equal(noteReview.getSnapshot().changes.has("b"), false);
  noteReview.connect(null);
});

test("an edit waiting on its card shows with the card's text and answers through it", async () => {
  const answers = [];
  const approval = { id: "a1", tool: "edit_block", perm: "block_edit",
    preview: { page_id: "p", block_id: "b", mode: "append", diff: [], text: "Draft\nThe key result." } };
  noteReview.ask(approval, async (decision) => { answers.push(decision); }, { permission: "Edit note blocks", kind: "notes" });
  const ask = noteReview.getSnapshot().asking.get("b");
  assert.deepEqual([ask.id, ask.text, ask.permission, ask.kind], ["a1", "Draft\nThe key result.", "Edit note blocks", "notes"]);
  await ask.decide("chat");
  assert.deepEqual(answers, ["chat"]);
  noteReview.settle("a2");
  assert.equal(noteReview.getSnapshot().asking.has("b"), true, "another card's answer leaves it");
  noteReview.settle("a1");
  assert.equal(noteReview.getSnapshot().asking.has("b"), false);
  // Only a note edit with its whole text is drawn in its note.
  noteReview.ask({ ...approval, tool: "create_block" }, () => {});
  noteReview.ask({ ...approval, preview: { ...approval.preview, text: undefined } }, () => {});
  assert.equal(noteReview.getSnapshot().asking.size, 0);
});

test("a change's row in the chat draws it again: its block's edits in a row, hidden, allowed on a card or not", () => {
  const messages = [
    { id: "q1", role: "user", text: "Tighten it" },
    { id: "r1", role: "ai", actions: [edit("b", "one", "two", { approval: "once" }), edit("c", "x", "y"), edit("b", "two", "three")] },
    { id: "q2", role: "user", text: "Again" },
    { id: "r2", role: "ai", actions: [edit("b", "three", "four"), edit("b", "typed", "five"), edit("c", "y", "z", { reverted: true })] },
  ];
  // The middle edit of a run brings the run: one → two → three → four.
  const steps = changeSteps(messages, "r1", 2, "conv");
  assert.deepEqual(steps.map((s) => [s.reply, s.index]), [["r1", 0], ["r1", 2], ["r2", 0]]);
  assert.ok(steps.every((s) => s.key === "conv"));
  // One that starts from text typed since stands alone; a reverted one is none.
  assert.deepEqual(changeSteps(messages, "r2", 1, "conv").map((s) => s.index), [1]);
  assert.deepEqual(changeSteps(messages, "r2", 2, "conv"), []);
  assert.deepEqual(changeSteps(messages, "q1", 0, "conv"), []);
  noteReview.hidePage("p");
  noteReview.show(steps);
  const shown = noteReview.getSnapshot().changes.get("b");
  assert.deepEqual([shown.before, shown.after, shown.steps.length], ["one", "four", 3], "the card's edit too, asked for");
  noteReview.hide("b");
  noteReview.show([]);
  assert.equal(noteReview.getSnapshot().changes.size, 0);
});
