// The agents' changes as one store (chat/noteReview.js): what each change
// draws in its notes and how a block's edits in a row chain; a page's
// suggestions read from the server while something on screen draws the
// page, the newest answer standing; a reply's change drawn as it lands when
// the journal took it as a suggestion; Accept and Reject (one change, a
// block's steps newest first, a page's all), Accept taking the journal's
// word; the refusal a Reject or an Accept meets; a change shown from its row
// in the chat until hidden or decided, every note of a paste; where a jump
// to a change lands; the account's Editing / Reviewing mode, a read never
// undoing a switch; and the states the reply's rows read.
import assert from "node:assert/strict";
import { test } from "node:test";
import { changeLanding, deletedAt, noteChanges, noteReview, objectFrame, surfaceKey, withChange } from "../src/chat/noteReview.js";

const edit = (id, before, after, extra = {}) => ({ kind: "edit", tool: "edit_block", block_id: id, page_id: "p",
  summary: "Edited a note", revert: { before, after }, ...extra });
const create = (id, after, extra = {}) => ({ kind: "create", tool: "create_block", block_id: id, page_id: "p",
  summary: "Added a note", revert: { after, parent: "p", position: "a0" }, ...extra });
const move = (id) => ({ kind: "move", tool: "move_block", block_id: id, page_id: "p", summary: "Moved a note", revert: { parent: "q" } });
// delete_block's record: its subtree's rows, the deleted note first
// ([id, parent_id, position, content, properties], gamma/ai_tools/notes.py).
const remove = (id, rows, extra = {}) => ({ kind: "delete", tool: "delete_block", block_id: id, page_id: "p",
  summary: "Deleted a note", revert: { blocks: rows }, ...extra });
const row = (changeId, action, { at = "2026-11-25T10:00:00.000000Z", state = "open" } = {}) => ({
  ...action, id: changeId, change_id: changeId, at, state, agent: { id: "tok", name: "Codex", for: "tim" } });
// The route's rows name their change by `id` alone (GET /api/ai/changes).
const served = (answer) => (answer?.changes ? { ...answer, changes: answer.changes.map(({ change_id, ...r }) => r) } : answer);

// A fake server: answers each request with `answer(path, body, init)` and keeps them.
function server(answer = () => ({})) {
  const asked = [];
  noteReview.setRequest(async (path, init) => {
    const body = init?.body ? JSON.parse(init.body) : null;
    asked.push([path, body]);
    return served(await answer(path, body, init));
  });
  return asked;
}
const snap = () => noteReview.getSnapshot();
const onPage = (rows) => server((path) => (path.startsWith("/ai/changes?page_id=") ? { changes: rows } : {}));
const pageReads = (asked) => asked.filter(([path]) => path.startsWith("/ai/changes?page_id=")).map(([path]) => path);
// Every promise on its way settled (a timer comes after them).
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

test("each change draws in its notes by its kind", () => {
  assert.deepEqual(noteChanges(edit("b", "old", "new")), [{ pageId: "p", blockId: "b", type: "edit", before: "old", after: "new" }]);
  assert.deepEqual(noteChanges(create("n", "fresh")), [{ pageId: "p", blockId: "n", type: "add", before: "", after: "fresh" }]);
  // a paste: each block it made, as new
  const paste = { tool: "create_block", block_id: "h", page_id: "p", revert: { blocks: [["h", "p", "a0", "## Head", "{}"],
    ["c", "h", "a0", "under it", "{}"]] } };
  assert.deepEqual(noteChanges(paste).map((c) => [c.blockId, c.type, c.after]), [["h", "add", "## Head"], ["c", "add", "under it"]]);
  // a move, a drawing and a highlight are their blocks, drawn as they are
  assert.equal(noteChanges({ tool: "move_block", block_id: "m", revert: { parent: "x" } })[0].type, "move");
  for (const tool of ["draw", "add_text_box", "place_picture", "highlight"]) {
    assert.equal(noteChanges({ tool, kind: "create", block_id: "o", revert: { after: "", parent: "p" } })[0].type, "object", tool);
  }
  // a deletion: the note that went, where it was, and how many went with it
  const gone = noteChanges(remove("d", [["d", "p", "a5", "Old note", "{}"], ["d1", "d", "a0", "under", "{}"], ["d2", "d1", "a0", "", "{}"]]));
  assert.deepEqual(gone, [{ pageId: "p", blockId: "d", type: "delete", before: "Old note", after: "", parentId: "p", position: "a5",
    count: 2, under: ["d1", "d2"] }]);
  for (const a of [
    edit("b", "same", "same"), edit("b", "a", "b", { error: true }), edit("b", "a", "b", { noop: true }),
    edit("b", "a", "b", { reverted: true }), edit("b", "a", "b", { revert: undefined }),
    remove("b", []), { kind: "read", tool: "read_block", block_id: "b" },
  ]) assert.deepEqual(noteChanges(a), [], JSON.stringify(a));
});

test("edits of one block in a row are one change; it waits while one step does", () => {
  let changes = withChange(new Map(), row("c1", edit("b", "one", "two"), { state: "kept" }));
  changes = withChange(changes, row("c2", edit("b", "two", "three"), { at: "2026-11-25T10:00:05.000000Z" }));
  const c = changes.get("b");
  assert.deepEqual([c.before, c.after, c.ids, c.agent, c.state, c.type, c.at],
    ["one", "three", ["c1", "c2"], "Codex", "open", "edit", "2026-11-25T10:00:05.000000Z"]);
  assert.deepEqual(c.stamp, { id: "tok", name: "Codex", for: "tim" }, "the agent's stamp, for its name in full");
  // Back where it started: nothing to draw once every step is kept; while
  // one waits it is drawn, so Accept and Reject reach it.
  const back = withChange(withChange(new Map(), row("k1", edit("b", "one", "two"), { state: "kept" })),
    row("k2", edit("b", "two", "one"), { state: "kept" }));
  assert.equal(back.has("b"), false);
  const waits = withChange(changes, row("c3", edit("b", "three", "one"))).get("b");
  assert.deepEqual([waits.before, waits.after, waits.ids, waits.state], ["one", "one", ["c1", "c2", "c3"], "open"]);
  // The block was changed in between (someone typed): the new edit is drawn
  // alone, the earlier step that waits kept in it.
  const fresh = withChange(changes, row("c4", edit("b", "typed", "four"), { state: "kept" })).get("b");
  assert.deepEqual([fresh.before, fresh.after, fresh.ids, fresh.state], ["typed", "four", ["c2", "c4"], "open"]);
  // An edit on top of a new note keeps the note new; a move joins the change.
  let made = withChange(withChange(new Map(), row("c5", create("n", "draft"))), row("c6", edit("n", "draft", "final")));
  made = withChange(made, row("c7", move("n")));
  assert.deepEqual([made.get("n").type, made.get("n").before, made.get("n").after, made.get("n").ids], ["add", "", "final", ["c5", "c6", "c7"]]);
  // An edit after a move is drawn as the edit, the move's id kept in it.
  const moved = withChange(withChange(new Map(), row("m1", move("b"))), row("e1", edit("b", "one", "two"))).get("b");
  assert.deepEqual([moved.type, moved.before, moved.after, moved.ids, moved.state], ["edit", "one", "two", ["m1", "e1"], "open"]);
  // A text box the agent placed and then edited stays the placed block.
  const box = { tool: "add_text_box", kind: "create", block_id: "t", page_id: "p", revert: { after: "", parent: "p", position: "a0" } };
  const boxed = withChange(withChange(new Map(), row("o1", box)), row("o2", edit("t", "Note", "Note!"))).get("t");
  assert.deepEqual([boxed.type, boxed.ids], ["object", ["o1", "o2"]]);
  assert.equal(withChange(changes, { kind: "read", tool: "read_block" }), changes, "nothing to add: the same map");
});

test("a deletion takes the note's own change and those of the notes that went with it", () => {
  let changes = withChange(new Map(), row("x1", edit("d", "a", "b"), { at: "2026-11-25T10:00:01Z" }));
  changes = withChange(changes, row("x2", create("c", "child"), { at: "2026-11-25T10:00:02Z" }));
  changes = withChange(changes, row("x3", remove("d", [["d", "p", "a1", "b", "{}"], ["c", "d", "a0", "child", "{}"]]),
    { at: "2026-11-25T10:00:03Z" }));
  assert.deepEqual([...changes.keys()], ["d"], "the child's row is gone with it: its change is the deletion's");
  const d = changes.get("d");
  assert.deepEqual([d.type, d.before, d.parentId, d.position, d.count, d.ids], ["delete", "b", "p", "a1", 1, ["x1", "x2", "x3"]]);
  // the place it had among its parent's notes now, by its sibling key
  const kids = [{ id: "k0", position: "a0" }, { id: "local" }, { id: "k2", position: "a2" }];
  assert.equal(deletedAt(kids, d), 2);
  assert.equal(deletedAt([], d), 0);
  assert.equal(deletedAt([{ id: "k0", position: "a0" }], d), 1);
});

test("a page's suggestions come from the server, and one decided elsewhere goes", async () => {
  let rows = [row("c1", edit("x", "a", "b")), row("c2", edit("y", "c", "d"))];
  const asked = server((path) => (path.startsWith("/ai/changes?page_id=") ? { changes: rows } : {}));
  await noteReview.load("p");
  assert.deepEqual(asked.map(([path]) => path), ["/ai/changes?page_id=p"]);
  assert.deepEqual([...snap().changes].map(([id, c]) => [id, c.state, c.agent]), [["x", "open", "Codex"], ["y", "open", "Codex"]]);
  assert.deepEqual([snap().states.get("c1"), snap().states.get("c2")], ["open", "open"]);
  // Accepted or rejected in another tab: the next read leaves it out, and
  // its state is read again when a row asks.
  rows = rows.slice(1);
  await noteReview.load("p");
  assert.deepEqual([...snap().changes.keys()], ["y"]);
  assert.equal(snap().states.has("c1"), false);
  noteReview.drop("p");
  assert.equal(snap().changes.size, 0);
});

test("a reply's change is drawn as it lands when the journal took it as a suggestion", async () => {
  server();
  const unwatch = noteReview.watch("p");
  await flush();
  noteReview.landed(edit("z", "e", "f", { change_id: "c9", change_state: "open" }));
  assert.deepEqual([snap().changes.get("z").after, snap().changes.get("z").state, snap().states.get("c9")], ["f", "open", "open"]);
  noteReview.landed(edit("w", "g", "h", { change_id: "c10", change_state: "kept" }));
  assert.deepEqual([snap().changes.has("w"), snap().states.get("c10")], [false, "kept"], "kept: written, nothing drawn");
  noteReview.landed(edit("v", "i", "j"));
  assert.equal(snap().changes.has("v"), false, "a change the journal did not take is not drawn");
  noteReview.landed({ tool: "rename_page", change_id: "c11", change_state: "kept" });
  assert.equal(snap().states.get("c11"), "kept", "one no note draws still has its state");
  // a deletion made as a suggestion is drawn where the note was
  noteReview.landed(remove("gone", [["gone", "p", "a3", "Bye", "{}"]], { change_id: "c13", change_state: "open" }));
  assert.equal(snap().changes.get("gone").type, "delete");
  // on a page off screen: its state alone, the page reads its suggestions when it comes
  noteReview.landed(edit("u", "k", "l", { change_id: "c12", page_id: "elsewhere", change_state: "open" }));
  assert.deepEqual([snap().changes.has("u"), snap().states.get("c12")], [false, "open"]);
  unwatch();
  await flush();
  assert.equal(snap().changes.size, 0, "the page left the screen: dropped");
});

test("Accept keeps a suggestion and it goes; one not open is not accepted again", async () => {
  const asked = onPage([row("k1", edit("a", "1", "2")), row("k2", edit("b", "3", "4"))]);
  await noteReview.load("p");
  assert.equal(await noteReview.acceptBlock("a"), null);
  assert.deepEqual(asked.at(-1), ["/ai/changes/k1", { decision: "keep", force: false }]);
  assert.deepEqual([snap().changes.has("a"), snap().states.get("k1")], [false, "kept"]);
  const before = asked.length;
  await noteReview.acceptBlock("a");
  assert.equal(asked.length, before, "gone: nothing to accept");
  await noteReview.acceptPage("p");
  assert.deepEqual(asked.at(-1), ["/ai/changes/k2", { decision: "keep", force: false }], "Accept all keeps what is left");
  assert.equal(snap().changes.size, 0);
  noteReview.drop("p");
});

test("Reject takes a change's steps back newest first, and a refusal waits by its buttons", async () => {
  let refuse = "";
  const asked = server((path, body) => {
    if (path.startsWith("/ai/changes?page_id=")) {
      return { changes: [row("u1", edit("b", "one", "two"), { at: "2026-11-25T10:00:01Z" }),
        row("u2", edit("b", "two", "three"), { at: "2026-11-25T10:00:02Z" })] };
    }
    if (path === `/ai/changes/${refuse}` && !body.force) {
      throw Object.assign(new Error("It was typed in since."), { status: 409, data: { conflict: "changed" } });
    }
    return { state: "undone" };
  });
  const refused = (err) => ({ text: err.message, force: "Undo anyway" });
  await noteReview.load("p");
  assert.equal(await noteReview.rejectBlock("b", { refused }), null);
  assert.deepEqual(asked.slice(1).map(([path, body]) => [path, body.decision]),
    [["/ai/changes/u2", "undo"], ["/ai/changes/u1", "undo"]]);
  assert.equal(snap().changes.has("b"), false, "rejected: no longer drawn");
  assert.equal(snap().states.get("u1"), "undone");

  // The newest step refused: nothing changed, the suggestion still shows,
  // and its buttons say why until forced.
  refuse = "u2";
  await noteReview.load("p");
  assert.deepEqual(await noteReview.rejectBlock("b", { refused }), { text: "It was typed in since.", force: "Undo anyway" });
  assert.equal(snap().changes.has("b"), true);
  assert.deepEqual(snap().refusals.get("b"), { text: "It was typed in since.", force: "Undo anyway" });
  assert.equal(await noteReview.rejectBlock("b", { force: true, refused }), null);
  assert.equal(snap().refusals.has("b"), false);
  noteReview.drop("p");
});

test("Reject all goes newest first and stops at the first that stops", async () => {
  server((path, body) => {
    if (path.startsWith("/ai/changes?page_id=")) {
      return { changes: [row("a1", edit("old", "x", "y"), { at: "2026-11-25T10:00:01Z" }),
        row("a2", edit("new", "p", "q"), { at: "2026-11-25T10:00:02Z" }),
        row("a3", remove("gone", [["gone", "p", "a9", "Bye", "{}"]]), { at: "2026-11-25T10:00:03Z" })] };
    }
    if (path === "/ai/changes/a1" && !body.force) throw Object.assign(new Error("Moved since."), { status: 409 });
    return { state: "undone" };
  });
  await noteReview.load("p");
  assert.equal(await noteReview.rejectPage("p"), "old");
  assert.deepEqual([...snap().changes.keys()], ["old"], "the newer ones went, the deletion first");
  assert.equal(snap().refusals.get("old").text, "Moved since.");
  noteReview.drop("p");
});

test("the states a reply's rows ask for are read together", async () => {
  const asked = server(() => ({ states: { s1: "kept", s2: "undone" } }));
  noteReview.want("s1");
  noteReview.want("s2");
  noteReview.want("s3");
  noteReview.want("s1");
  await flush();
  assert.deepEqual(asked.map(([path]) => path), ["/ai/changes?ids=s1%2Cs2%2Cs3"]);
  assert.deepEqual([snap().states.get("s1"), snap().states.get("s2"), snap().states.get("s3")], ["kept", "undone", ""]);
  noteReview.want("s1");
  await flush();
  assert.equal(asked.length, 1, "a known state is not read again");
});

test("a placed block an agent made is framed where it sits on its surface", () => {
  const made = (tool, props, extra = {}) => ({ tool, kind: "create", block_id: "o", page_id: "pg", change_id: "x",
    revert: { after: "", parent: "pg", position: "a0", props }, ...extra });
  const pos = { pageNumber: 2, width: 600, height: 800, boundingRect: { x1: 10, y1: 20, x2: 110, y2: 40 }, rects: [] };
  assert.deepEqual(objectFrame(made("highlight", { pdf_position: pos })), { surface: "pg:2", frame: [600, 800], box: [10, 20, 110, 40] });
  assert.deepEqual(objectFrame(made("draw", { ink_url: "/api/uploads/i.ink", pdf_position: pos })).surface, "pg:2");
  assert.deepEqual(objectFrame(made("add_text_box", { text_box: { x: 60, y: 720, w: 180, h: 20 }, pdf_page: 2 })),
    { surface: "pg:2", frame: null, box: [60, 720, 240, 740] });
  // on a page of paper: the sheet it was made under
  const onSheet = objectFrame(made("place_picture", { picture: { x: 1, y: 2, w: 3, h: 4 } }, { revert: {
    after: "", parent: "sheet-1", position: "a0", props: { picture: { x: 1, y: 2, w: 3, h: 4 } } } }));
  assert.equal(onSheet.surface, "sheet-1");
  // and a sheet's layer reads it by the key the store files it under (markup/ReviewLayer.jsx)
  assert.equal(surfaceKey("pg", "sheet-1"), onSheet.surface);
  assert.equal(surfaceKey("pg", 2), objectFrame(made("highlight", { pdf_position: pos })).surface);
  // a drawing on a page of paper keeps its strokes in its file: its row draws it alone
  assert.equal(objectFrame(made("draw", { ink_url: "/api/uploads/i.ink" })), null);
  assert.equal(objectFrame(edit("b", "a", "b")), null);
});

test("a jump to a change lands on its note, a deleted note's struck row under its parent, a placed box's PDF page", () => {
  const at = (where) => [where.pageId, where.blockId, where.struck, where.pdfPage];
  assert.deepEqual(at(changeLanding(edit("a", "1", "2"))), ["p", "a", "", 0]);
  // a deleted note: its struck row, under its parent, the page's root for one at the top
  const gone = remove("c", [["c", "b", "a1", "Gone"], ["d", "c", "a0", "Under"]]);
  assert.deepEqual(at(changeLanding(gone)), ["p", "b", "c", 0]);
  assert.deepEqual(at(changeLanding(remove("c", [["c", "", "a1", "Gone"]]))), ["p", "p", "c", 0]);
  // from what the notes draw as from the record: the same place
  assert.deepEqual(at(changeLanding(withChange(new Map(), gone).get("c"))), ["p", "b", "c", 0]);
  // a text box on a PDF page lands at that page; a highlight and a drawing through their note
  const placed = (tool, props) => ({ tool, kind: "create", block_id: "o", page_id: "p", revert: { after: "", parent: "p", props } });
  assert.deepEqual(at(changeLanding(placed("add_text_box", { text_box: { x: 1, y: 2, w: 3, h: 4 }, pdf_page: 3 }))), ["p", "o", "", 3]);
  const pos = { pageNumber: 2, width: 600, height: 800, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 } };
  assert.deepEqual(at(changeLanding(placed("draw", { ink_url: "/i.ink", pdf_position: pos }))), ["p", "o", "", 0]);
  // a record no note draws still names its note
  assert.deepEqual(at(changeLanding({ tool: "edit_block", block_id: "z", page_id: "q" })), ["q", "z", "", 0]);
});

test("a move and the edit after it are one suggestion, and Accept all keeps both", async () => {
  const asked = server((path, body) => (path.startsWith("/ai/changes?page_id=")
    ? { changes: [row("m1", move("b"), { at: "2026-11-25T10:00:01Z" }), row("e1", edit("b", "one", "two"), { at: "2026-11-25T10:00:02Z" })] }
    : path === "/ai/changes/keep" ? { kept: body.ids } : {}));
  await noteReview.load("p");
  assert.deepEqual(snap().changes.get("b").ids, ["m1", "e1"]);
  await noteReview.acceptPage("p");
  assert.deepEqual(asked.at(-1), ["/ai/changes/keep", { ids: ["m1", "e1"] }]);
  assert.deepEqual([snap().changes.has("b"), snap().states.get("e1")], [false, "kept"]);
  noteReview.drop("p");
});

test("Accept takes the journal's word: a change undone elsewhere meanwhile is never taken as kept", async () => {
  const made = { g1: edit("a", "1", "2"), g2: edit("b", "3", "4"), g3: edit("c", "5", "6") };
  const journal = new Map(Object.keys(made).map((id) => [id, "open"]));
  const asked = server((path, body) => {
    if (path.startsWith("/ai/changes?page_id=")) {
      return { changes: [...journal].filter(([, state]) => state === "open").map(([id, state]) => row(id, made[id], { state })) };
    }
    if (path === "/ai/changes/keep") {
      const kept = body.ids.filter((id) => journal.get(id) === "open");
      for (const id of kept) journal.set(id, "kept");
      return { kept };
    }
    const id = decodeURIComponent(path.split("/").pop());
    const was = journal.get(id);
    if (was === "open") journal.set(id, "kept");
    return was === "open" ? { state: "kept", page_id: "p" } : { state: was, page_id: "p", noop: true };
  });
  const unwatch = noteReview.watch("p");
  await flush();
  // undone in another tab before this one heard of it
  journal.set("g1", "undone");
  journal.set("g3", "undone");
  await noteReview.acceptBlock("a");
  assert.deepEqual([snap().states.get("g1"), snap().changes.has("a")], ["undone", false], "one: the answer's state");
  await noteReview.accept(["g2", "g3"]);
  await flush();
  assert.deepEqual([snap().states.get("g2"), snap().changes.has("b")], ["kept", false]);
  assert.notEqual(snap().states.get("g3"), "kept", "the batch: only the ids it kept");
  assert.equal(snap().changes.has("c"), false, "its page read again: undone, it is not drawn");
  assert.equal(pageReads(asked).length, 2);
  unwatch();
  await flush();
});

test("an Accept that fails says why by the block's buttons, as a Reject's refusal does", async () => {
  server((path) => {
    if (path.startsWith("/ai/changes?page_id=")) return { changes: [row("f1", edit("a", "1", "2"))] };
    throw new Error("The server is away.");
  });
  await noteReview.load("p");
  const why = { text: "The server is away.", force: "", diff: null };
  assert.deepEqual(await noteReview.acceptBlock("a"), why);
  assert.deepEqual([snap().refusals.get("a"), snap().changes.get("a").state], [why, "open"]);
  noteReview.clearRefusal("a");
  noteReview.drop("p");
});

test("a change shown from its row in the chat stays until hidden or decided, one at a time", async () => {
  server(() => ({ state: "kept" }));
  // a kept change: drawn from the row's own record
  assert.equal(noteReview.reveal(edit("a", "1", "2", { change_id: "r1" })), "a");
  const shown = snap().changes.get("a");
  assert.deepEqual([shown.before, shown.after, shown.state, shown.shown, snap().shown.blockId], ["1", "2", "kept", true, "a"]);
  // the next one shown takes its place
  assert.equal(noteReview.reveal(edit("b", "3", "4", { change_id: "r2" })), "b");
  assert.deepEqual([snap().changes.has("a"), snap().changes.get("b")?.shown], [false, true]);
  noteReview.hide();
  assert.deepEqual([snap().changes.has("b"), snap().shown], [false, null]);
  // decided (here, or redone from its row): it goes
  noteReview.reveal(edit("c", "5", "6", { change_id: "r3" }));
  await noteReview.decide("r3", "redo");
  assert.deepEqual([snap().changes.has("c"), snap().shown], [false, null]);
  // a paste of several notes draws every one of them
  const paste = { ...create("n1", "x"), revert: { blocks: [["n1", "p", "a0", "First"], ["n2", "p", "a1", "Second"]] } };
  assert.equal(noteReview.reveal({ ...paste, change_id: "r5" }), "n1");
  assert.deepEqual([snap().changes.get("n1")?.after, snap().changes.get("n2")?.after, snap().changes.get("n2")?.shown],
    ["First", "Second", true]);
  noteReview.hide();
  assert.deepEqual([snap().changes.has("n1"), snap().changes.has("n2")], [false, false]);
  // one undone (by the journal's word, or the mark its conversation saved)
  // shows nothing; one no note draws neither
  server(() => ({ state: "undone" }));
  await noteReview.decide("u1", "undo");
  assert.equal(noteReview.reveal(edit("e", "a", "b", { change_id: "u1" })), "");
  assert.equal(noteReview.reveal(edit("e", "a", "b", { reverted: true })), "");
  assert.equal(noteReview.reveal({ tool: "rename_page", change_id: "r4" }), "");
  assert.equal(snap().shown, null);
});

test("a press on a suggestion's row in the chat lands on the suggestion drawn already", async () => {
  onPage([row("s1", edit("a", "1", "2"))]);
  await noteReview.load("p");
  assert.equal(noteReview.reveal(edit("a", "1", "2", { change_id: "s1" })), "a");
  assert.deepEqual([snap().shown, snap().changes.get("a").state], [null, "open"], "not shown twice: the suggestion is drawn");
  // one the journal holds open on a page not read yet: drawn once it is read
  noteReview.landed(edit("z", "1", "2", { change_id: "s2", page_id: "later", change_state: "open" }));
  assert.equal(noteReview.reveal(edit("z", "1", "2", { change_id: "s2", page_id: "later" })), "z");
  assert.equal(snap().shown, null);
  noteReview.drop("p");
});

test("a page is read once while things on screen draw it, and dropped once the last goes", async () => {
  const asked = onPage([row("w1", edit("a", "1", "2"))]);
  const header = noteReview.watch("p");
  const pdfPage = noteReview.watch("p");
  await flush();
  assert.equal(pageReads(asked).length, 1, "the second reads nothing more");
  assert.equal(snap().changes.has("a"), true);
  header();
  header(); // a second call is nothing
  await flush();
  assert.equal(snap().changes.has("a"), true, "one of two gone: the rows stay");
  // gone and back in one render (a surface mounted again): kept, not read again
  pdfPage();
  const again = noteReview.watch("p");
  await flush();
  assert.deepEqual([pageReads(asked).length, snap().changes.has("a")], [1, true]);
  again();
  await flush();
  assert.equal(snap().changes.has("a"), false, "the last gone: dropped");
  assert.equal(pageReads(asked).length, 1);
});

test("the server's nudge reads again a page on screen alone", async () => {
  const asked = onPage([]);
  globalThis.window = new EventTarget();
  const nudge = (pageId) => globalThis.window.dispatchEvent(new CustomEvent("gamma:ai-changes", { detail: { pageId } }));
  try {
    const unwatch = noteReview.watch("p");
    await flush();
    nudge("p");
    nudge("elsewhere");
    await flush();
    assert.deepEqual(pageReads(asked), ["/ai/changes?page_id=p", "/ai/changes?page_id=p"]);
    unwatch();
    await flush();
    nudge("p");
    await flush();
    assert.equal(pageReads(asked).length, 2, "nothing on screen: no nudge is heard");
  } finally {
    delete globalThis.window;
  }
});

test("the newest answer about a page stands, whichever comes first; one sent before the page was dropped is left", async () => {
  const answers = [];
  noteReview.setRequest(() => new Promise((resolve) => answers.push(resolve)));
  const older = noteReview.load("p");
  const newer = noteReview.load("p");
  answers[1](served({ changes: [row("o2", edit("c", "3", "4"))] }));
  await newer;
  answers[0](served({ changes: [row("o1", edit("b", "1", "2"))] }));
  await older;
  assert.deepEqual([snap().changes.has("b"), snap().changes.has("c")], [false, true]);
  const late = noteReview.load("p");
  noteReview.drop("p");
  answers[2](served({ changes: [row("o3", edit("d", "5", "6"))] }));
  await late;
  assert.deepEqual([snap().pages.has("p"), snap().changes.has("d")], [false, false], "dropped: the read on its way is left");
});

test("a state is not read again while its read is on its way, nor straight after a failed read", async () => {
  const clock = { now: 1_000_000 };
  noteReview.setClock({ now: () => clock.now });
  try {
    const asked = [];
    let answer = null;
    noteReview.setRequest((path) => {
      asked.push(path);
      return new Promise((resolve, reject) => { answer = { resolve, reject }; });
    });
    noteReview.want("slow1");
    await flush();
    noteReview.want("slow1");
    await flush();
    assert.equal(asked.length, 1, "on its way: not asked again");
    answer.resolve({ states: { slow1: "kept" } });
    await flush();
    assert.equal(snap().states.get("slow1"), "kept");
    noteReview.want("fail1");
    await flush();
    answer.reject(new Error("The server is away."));
    await flush();
    noteReview.want("fail1");
    await flush();
    assert.equal(asked.length, 2, "failed: not asked again on the next render");
    clock.now += 31_000;
    noteReview.want("fail1");
    await flush();
    assert.equal(asked.length, 3, "asked again once a while has passed");
    answer.resolve({ states: { fail1: "open" } });
    await flush();
    assert.equal(snap().states.get("fail1"), "open");
  } finally {
    noteReview.setClock();
  }
});

test("the account's mode is read from its pref and switched at once; a failed switch is put back", async () => {
  let stored = null;
  let fail = false;
  const asked = server((path, body, init) => {
    assert.equal(path, "/prefs/ai-review");
    if (init?.method === "PUT") {
      if (fail) throw new Error("The server is away.");
      stored = body.value;
      return { key: "ai-review" };
    }
    return { key: "ai-review", value: stored };
  });
  await noteReview.loadMode();
  assert.equal(snap().reviewing, false, "none stored: Editing");
  const switching = noteReview.setReviewing(true);
  assert.equal(snap().reviewing, true, "shown before the server answers");
  await switching;
  assert.deepEqual([asked.at(-1), stored], [["/prefs/ai-review", { value: true }], true]);
  await noteReview.loadMode();
  assert.equal(snap().reviewing, true);
  fail = true;
  await assert.rejects(noteReview.setReviewing(false), /The server is away/);
  assert.equal(snap().reviewing, true, "the write failed: as it was");
  // a read on its way when the mode is switched leaves the switch standing
  fail = false;
  stored = true;
  const reading = noteReview.loadMode();
  await noteReview.setReviewing(false);
  await reading;
  assert.equal(snap().reviewing, false);
});

test("a read of the mode sent while a switch is being written never undoes it, and a failed switch is still put back", async () => {
  // The server answers each request when the test lets it, in any order.
  const waiting = [];
  server((path, body, init) => new Promise((resolve, reject) => waiting.push({ put: init?.method === "PUT", resolve, reject })));
  const answer = (i, value) => waiting[i].resolve(waiting[i].put ? { key: "ai-review" } : { key: "ai-review", value });
  assert.equal(snap().reviewing, false);
  const switching = noteReview.setReviewing(true);
  const reading = noteReview.loadMode(); // the menu opening while the write is on its way
  answer(1, false); // the server read the pref before it wrote it
  await flush();
  assert.equal(snap().reviewing, true, "the read answered while the write was on its way is left");
  answer(0);
  await Promise.all([switching, reading]);
  assert.equal(snap().reviewing, true);
  // a read sent before the switch, answered after the write: left as well
  const before = noteReview.loadMode();
  const back = noteReview.setReviewing(false);
  answer(3);
  await back;
  answer(2, true);
  await before;
  assert.equal(snap().reviewing, false, "the read sent before the switch was overtaken by it");
  // a failed write puts the mode back, though a read was sent meanwhile
  const failing = noteReview.setReviewing(true);
  const meanwhile = noteReview.loadMode();
  waiting[4].reject(new Error("The server is away."));
  await assert.rejects(failing, /The server is away/);
  assert.equal(snap().reviewing, false, "put back as it was");
  answer(5, false);
  await meanwhile;
  assert.equal(snap().reviewing, false);
  // once nothing is being written, a read stands
  const later = noteReview.loadMode();
  answer(6, true);
  await later;
  assert.equal(snap().reviewing, true, "a read with no write about stands");
  const reset = noteReview.setReviewing(false);
  answer(7);
  await reset;
});
