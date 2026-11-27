// The agents' changes as one store (chat/noteReview.js): what each change
// draws in its notes and how a block's edits in a row chain; a page's
// changes read from the server while something on screen draws the page,
// waiting for review or kept, the newest answer standing; a reply's change
// drawn as it lands, kept when its card allowed it; Keep, Undo and Clear
// (one change, a block's steps newest first, a page's all), Keep taking the
// journal's word; the refusal an Undo or a Keep meets; a kept change's mark
// unfolded by a press or from its row in the chat, and folded once its
// change goes; the calls waiting on their cards, drawn where they would
// land; and the states the reply's rows read.
import assert from "node:assert/strict";
import { test } from "node:test";
import { isTextChange, noteChanges, noteReview, objectFrame, surfaceKey, withChange } from "../src/chat/noteReview.js";

const edit = (id, before, after, extra = {}) => ({ kind: "edit", tool: "edit_block", block_id: id, page_id: "p",
  summary: "Edited a note", revert: { before, after }, ...extra });
const create = (id, after, extra = {}) => ({ kind: "create", tool: "create_block", block_id: id, page_id: "p",
  summary: "Added a note", revert: { after, parent: "p", position: "a0" }, ...extra });
const move = (id) => ({ kind: "move", tool: "move_block", block_id: id, page_id: "p", summary: "Moved a note", revert: { parent: "q" } });
const row = (changeId, action, { at = "2026-11-25T10:00:00.000000Z", state = "open" } = {}) => ({
  ...action, id: changeId, change_id: changeId, at, state, agent: { name: "Codex" } });
// The route's rows name their change by `id` alone (routers/ai.py ai_changes).
const served = (answer) => (answer?.changes ? { ...answer, changes: answer.changes.map(({ change_id, ...r }) => r) } : answer);

// A fake server: answers each request with `answer(path, body)` and keeps them.
function server(answer = () => ({})) {
  const asked = [];
  noteReview.setRequest(async (path, init) => {
    const body = init?.body ? JSON.parse(init.body) : null;
    asked.push([path, body]);
    return served(await answer(path, body));
  });
  return asked;
}
const snap = () => noteReview.getSnapshot();
const onPage = (rows) => server((path) => (path.startsWith("/ai/changes?page_id=") ? { changes: rows } : {}));
const pageReads = (asked) => asked.filter(([path]) => path.startsWith("/ai/changes?page_id=")).map(([path]) => path);
// Every promise on its way settled (a timer comes after them).
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
// A clock the test moves by hand (`tick`), and the store's own back.
function handClock() {
  const clock = { now: 1_000_000, timer: null };
  noteReview.setClock({ now: () => clock.now, set: (fn, ms) => { clock.timer = { fn, at: clock.now + ms }; return clock.timer; },
    clear: () => { clock.timer = null; } });
  clock.tick = (ms) => {
    clock.now += ms;
    if (clock.timer && clock.timer.at <= clock.now) { const { fn } = clock.timer; clock.timer = null; fn(); }
  };
  return clock;
}
const realClock = () => noteReview.setClock({ now: () => Date.now(),
  set: (fn, ms) => { const id = setTimeout(fn, ms); id.unref?.(); return id; }, clear: (id) => clearTimeout(id) });

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
  for (const a of [
    edit("b", "same", "same"), edit("b", "a", "b", { error: true }), edit("b", "a", "b", { noop: true }),
    edit("b", "a", "b", { reverted: true }), edit("b", "a", "b", { revert: undefined }),
    { kind: "delete", tool: "delete_block", block_id: "b", revert: { blocks: [] } },
    { kind: "read", tool: "read_block", block_id: "b" },
  ]) assert.deepEqual(noteChanges(a), [], JSON.stringify(a));
  assert.deepEqual(["edit", "add", "move", "object"].map(isTextChange), [true, true, false, false], "a text: an edit, a new note");
});

test("edits of one block in a row are one change; it waits while one step does", () => {
  let changes = withChange(new Map(), row("c1", edit("b", "one", "two"), { state: "kept" }));
  changes = withChange(changes, row("c2", edit("b", "two", "three")));
  const c = changes.get("b");
  assert.deepEqual([c.before, c.after, c.ids, c.agent, c.state, c.type], ["one", "three", ["c1", "c2"], "Codex", "open", "edit"]);
  // Back where it started: nothing to mark once every step is kept; while
  // one waits it is drawn, so Keep and Undo reach it.
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

test("a page's changes come from the server, kept ones too, and one decided elsewhere goes", async () => {
  let rows = [row("c1", edit("x", "a", "b")), row("c2", edit("y", "c", "d"), { state: "kept" })];
  const asked = server((path) => (path.startsWith("/ai/changes?page_id=") ? { changes: rows } : {}));
  await noteReview.load("p");
  assert.deepEqual(asked.map(([path]) => path), ["/ai/changes?page_id=p&marks_for=60"], "a minute's marks, by default");
  assert.deepEqual([...snap().changes].map(([id, c]) => [id, c.state, c.agent]), [["x", "open", "Codex"], ["y", "kept", "Codex"]]);
  assert.deepEqual([snap().states.get("c1"), snap().states.get("c2")], ["open", "kept"]);
  // Kept and cleared, or undone, in another tab: the next read leaves it out,
  // and its state is read again when a row asks.
  rows = rows.slice(1);
  await noteReview.load("p");
  assert.deepEqual([...snap().changes.keys()], ["y"]);
  assert.equal(snap().states.has("c1"), false);
  noteReview.drop("p");
  assert.equal(snap().changes.size, 0);
});

test("a reply's change is drawn as it lands: waiting when unasked, kept to its mark when its card allowed it", async () => {
  server();
  const unwatch = noteReview.watch("p");
  await flush();
  noteReview.landed(edit("z", "e", "f", { change_id: "c9" }));
  assert.deepEqual([snap().changes.get("z").after, snap().changes.get("z").state, snap().states.get("c9")], ["f", "open", "open"]);
  noteReview.landed(edit("w", "g", "h", { change_id: "c10", approval: "once" }));
  assert.deepEqual([snap().changes.get("w").state, snap().states.get("c10")], ["kept", "kept"]);
  noteReview.landed(edit("v", "i", "j"));
  assert.equal(snap().changes.has("v"), false, "a change the journal did not take is not drawn");
  noteReview.landed({ tool: "rename_page", change_id: "c11", approval: "once" });
  assert.equal(snap().states.get("c11"), "kept", "one no note draws still has its state");
  // on a page off screen: its state alone, the page reads its changes when it comes
  noteReview.landed(edit("u", "k", "l", { change_id: "c12", page_id: "elsewhere" }));
  assert.deepEqual([snap().changes.has("u"), snap().states.get("c12")], [false, "open"]);
  unwatch();
  await flush();
  assert.equal(snap().changes.size, 0, "the page left the screen: dropped");
});

test("Keep folds a waiting change to its mark; Clear takes the marks away", async () => {
  const asked = onPage([row("k1", edit("a", "1", "2")), row("k2", edit("b", "3", "4")), row("k3", edit("c", "5", "6"), { state: "kept" })]);
  await noteReview.load("p");
  noteReview.focus("a"); // the strip stepped to it
  await noteReview.keepBlock("a");
  assert.deepEqual(asked.at(-1), ["/ai/changes/k1", { decision: "keep" }]);
  assert.equal(snap().changes.get("a").state, "kept", "kept: still marked, folded");
  assert.equal(snap().expanded.has("a"), false, "folded to its mark, though it was stepped to");
  assert.equal(snap().states.get("k1"), "kept");
  const before = asked.length;
  await noteReview.keepBlock("c");
  assert.equal(asked.length, before, "a kept one is not kept again");
  await noteReview.keepPage("p");
  assert.deepEqual(asked.at(-1), ["/ai/changes/k2", { decision: "keep" }], "Keep All keeps what waits");
  await noteReview.clearPage("p");
  assert.deepEqual(asked.at(-1)[0], "/ai/changes/clear");
  assert.deepEqual(asked.at(-1)[1].ids.sort(), ["k1", "k2", "k3"]);
  assert.equal(snap().changes.size, 0);
});

test("a kept change's mark unfolds and folds; its row in the chat unfolds it too", async () => {
  onPage([row("m1", edit("a", "1", "2"), { state: "kept" })]);
  await noteReview.load("p");
  assert.equal(snap().expanded.has("a"), false);
  noteReview.toggle("a");
  assert.equal(snap().expanded.has("a"), true);
  noteReview.toggle("a");
  assert.equal(snap().expanded.has("a"), false);
  noteReview.reveal(edit("a", "1", "2", { change_id: "m1" }));
  assert.equal(snap().expanded.has("a"), true, "the journal's change, unfolded");
  // One the journal does not hold (saved before it): drawn from the row's own
  // record, and taken back as the row takes it.
  const undone = [];
  noteReview.reveal(edit("old", "x", "y"), { undo: async ({ force }) => { undone.push(force); } });
  const shown = snap().changes.get("old");
  assert.deepEqual([shown.before, shown.after, shown.state, snap().expanded.has("old")], ["x", "y", "kept", true]);
  assert.equal(await noteReview.undoBlock("old"), null);
  assert.deepEqual(undone, [false]);
  assert.equal(snap().changes.has("old"), false);
  noteReview.focus("");
  noteReview.drop("p");
});

test("Undo takes a change's steps back newest first, and a refusal waits on its bar", async () => {
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
  assert.equal(await noteReview.undoBlock("b", { refused }), null);
  assert.deepEqual(asked.slice(1).map(([path, body]) => [path, body.decision]),
    [["/ai/changes/u2", "undo"], ["/ai/changes/u1", "undo"]]);
  assert.equal(snap().changes.has("b"), false, "undone: no longer drawn");
  assert.equal(snap().states.get("u1"), "undone");

  // The newest step refused: nothing changed, the change still shows, and
  // its bar says why until forced.
  refuse = "u2";
  await noteReview.load("p");
  assert.deepEqual(await noteReview.undoBlock("b", { refused }), { text: "It was typed in since.", force: "Undo anyway" });
  assert.equal(snap().changes.has("b"), true);
  assert.deepEqual(snap().refusals.get("b"), { text: "It was typed in since.", force: "Undo anyway" });
  assert.equal(await noteReview.undoBlock("b", { force: true, refused }), null);
  assert.equal(snap().refusals.has("b"), false);
});

test("Undo All goes newest first over what waits and stops at the first that stops", async () => {
  server((path, body) => {
    if (path.startsWith("/ai/changes?page_id=")) {
      return { changes: [row("a1", edit("old", "x", "y"), { at: "2026-11-25T10:00:01Z" }),
        row("a2", edit("new", "p", "q"), { at: "2026-11-25T10:00:02Z" }),
        row("a3", edit("kept", "k", "l"), { at: "2026-11-25T10:00:03Z", state: "kept" })] };
    }
    if (path === "/ai/changes/a1" && !body.force) throw Object.assign(new Error("Moved since."), { status: 409 });
    return { state: "undone" };
  });
  await noteReview.load("p");
  assert.equal(await noteReview.undoPage("p"), "old");
  assert.deepEqual([...snap().changes.keys()].sort(), ["kept", "old"], "the newer one went; the kept one is not Undo All's");
  assert.equal(snap().refusals.get("old").text, "Moved since.");
  noteReview.drop("p");
  noteReview.clearRefusal("old");
});

test("the states a reply's rows ask for are read together", async () => {
  const asked = server(() => ({ states: { s1: "kept", s2: "undone" } }));
  noteReview.want("s1");
  noteReview.want("s2");
  noteReview.want("s3");
  noteReview.want("s1");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(asked.map(([path]) => path), ["/ai/changes?ids=s1%2Cs2%2Cs3"]);
  assert.deepEqual([snap().states.get("s1"), snap().states.get("s2"), snap().states.get("s3")], ["kept", "undone", ""]);
  noteReview.want("s1");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(asked.length, 1, "a known state is not read again");
});

test("a call waiting on its card is drawn where it would land, and answers through the card", async () => {
  const answers = [];
  const decide = async (decision) => { answers.push(decision); };
  const card = (id, tool, preview, perm = "block_edit") => ({ id, tool, perm, preview: { page_id: "p", ...preview } });
  noteReview.ask(card("e1", "edit_block", { block_id: "b", text: "Draft\nThe key result." }), decide,
    { permission: "Edit note blocks", kind: "notes" });
  noteReview.ask(card("d1", "delete_block", { block_id: "gone", children: 2 }), decide);
  noteReview.ask(card("m1", "move_block", { block_id: "mv", parent_id: "q", parent: "Methods" }), decide);
  noteReview.ask(card("n1", "create_block", { parent_id: "p", after_id: "b", text: "A new note", count: 1 }), decide);
  noteReview.ask(card("h1", "highlight", { pdf_page: 3, shape: { kind: "highlight", frame: [600, 800], rects: [[1, 2, 3, 4]] } },
    "comment"), decide);
  noteReview.ask(card("s1", "draw", { sheet_id: "sheet", shape: { kind: "ink", frame: [400, 600], strokes: [] } }, "draw"), decide);
  const asking = snap().asking;
  assert.deepEqual([...asking.keys()], ["e1", "d1", "m1", "n1", "h1", "s1"]);
  const e1 = asking.get("e1");
  assert.deepEqual([e1.tool, e1.preview.text, e1.permission, e1.kind], ["edit_block", "Draft\nThe key result.", "Edit note blocks", "notes"]);
  await e1.decide("chat");
  assert.deepEqual(answers, ["chat"]);
  assert.equal(surfaceKey("p", 3), "p:3");
  assert.equal(surfaceKey("p", "sheet"), "sheet");
  // Nothing to draw it by: a card with no place, an edit without its whole text.
  noteReview.ask(card("x1", "rename_page", { title: "A" }), decide);
  noteReview.ask(card("x2", "edit_block", { block_id: "b" }), decide);
  noteReview.ask(card("x3", "highlight", { place: { kind: "lines" } }, "comment"), decide);
  assert.equal(snap().asking.size, 6);
  for (const id of ["e1", "d1", "m1", "n1", "h1", "s1", "nope"]) noteReview.settle(id);
  assert.equal(snap().asking.size, 0);
});

test("a kept change's mark stays as long as the account lets it, or while it is unfolded; 0 keeps it till cleared", async () => {
  const { tick } = handClock();
  try {
    noteReview.setMarkLife(60_000);
    const asked = [];
    // the server says how long ago each was kept; the page is read for a minute's marks
    noteReview.setRequest(async (path) => {
      asked.push(path);
      return served({ changes: [{ ...row("t1", edit("a", "1", "2"), { state: "kept" }), kept_ago: 30 }, row("t2", edit("b", "3", "4"))] });
    });
    await noteReview.load("p");
    assert.equal(asked[0], "/ai/changes?page_id=p&marks_for=60");
    assert.deepEqual([...snap().changes.keys()], ["a", "b"]);
    tick(31_000);
    assert.deepEqual([...snap().changes.keys()], ["b"], "its minute is up; the change waiting for review stays");
    // an unfolded one stays while it is unfolded, and goes as it folds
    noteReview.setRequest(async () => served({ changes: [{ ...row("t1", edit("a", "1", "2"), { state: "kept" }), kept_ago: 50 }] }));
    await noteReview.load("p");
    noteReview.toggle("a", true);
    tick(20_000);
    assert.equal(snap().changes.has("a"), true, "unfolded: it stays");
    noteReview.toggle("a", false);
    assert.equal(snap().changes.has("a"), false, "folded after its time: gone");
    // till cleared: no time is up
    noteReview.setMarkLife(0);
    assert.equal(snap().changes.has("a"), true);
    tick(10 * 60_000);
    assert.equal(snap().changes.has("a"), true);
  } finally {
    realClock();
    noteReview.setMarkLife(60_000);
    noteReview.drop("p");
  }
});

test("an assistant's question over MCP is read with its page's changes, drawn by its name, and answered as a card", async () => {
  const preview = { page_id: "p", block_id: "b", text: "Draft" };
  // a move to another page is drawn on the page the note is on now
  const moving = { id: "ap3", tool: "move_block", perm: "block_move", agent: "Codex",
    preview: { page_id: "q", src_page_id: "p", block_id: "mv", parent_id: "x" } };
  const all = [{ id: "ap1", tool: "edit_block", perm: "block_edit", preview, agent: "Codex" },
    { id: "ap2", tool: "rename_page", perm: "rename", preview: { page_id: "p", title: "A" }, agent: "Codex" }, moving];
  let asks = all;
  const asked = server((path) => (path.startsWith("/ai/changes?page_id=") ? { changes: [], asks } : { ok: true }));
  await noteReview.load("p");
  const ask = snap().asking.get("ap1");
  assert.deepEqual([ask.remote, ask.agent, ask.tool], [true, "Codex", "edit_block"]);
  assert.equal(snap().asking.has("ap2"), false, "nothing to draw it by: its client alone asks");
  assert.equal(snap().asking.has("ap3"), true);
  await ask.decide("deny", "not that note");
  assert.deepEqual(asked.at(-1), ["/ai/approvals/ap1", { decision: "deny", note: "not that note" }]);
  // answered (here or in its client): the next read leaves it out, the move too; a chat card stays
  noteReview.ask({ id: "local", tool: "edit_block", perm: "block_edit", preview: { page_id: "p", block_id: "c", text: "x" } }, () => {});
  asks = [];
  await noteReview.load("p");
  assert.deepEqual([...snap().asking.keys()], ["local"]);
  // the page left the screen: its questions go with its changes
  asks = all;
  await noteReview.load("p");
  noteReview.drop("p");
  assert.deepEqual([...snap().asking.keys()], ["local"]);
  noteReview.settle("local");
});

test("a placed block an agent made is marked where it sits on its surface", () => {
  const made = (tool, props, extra = {}) => ({ tool, kind: "create", block_id: "o", page_id: "pg", change_id: "x",
    revert: { after: "", parent: "pg", position: "a0", props }, ...extra });
  const pos = { pageNumber: 2, width: 600, height: 800, boundingRect: { x1: 10, y1: 20, x2: 110, y2: 40 }, rects: [] };
  assert.deepEqual(noteChanges(made("highlight", { pdf_position: pos }))[0].type, "object");
  assert.deepEqual(objectFrame(made("highlight", { pdf_position: pos })), { surface: "pg:2", frame: [600, 800], box: [10, 20, 110, 40] });
  assert.deepEqual(objectFrame(made("draw", { ink_url: "/api/uploads/i.ink", pdf_position: pos })).surface, "pg:2");
  assert.deepEqual(objectFrame(made("add_text_box", { text_box: { x: 60, y: 720, w: 180, h: 20 }, pdf_page: 2 })),
    { surface: "pg:2", frame: null, box: [60, 720, 240, 740] });
  // on a page of paper: the sheet it was made under
  assert.equal(objectFrame(made("place_picture", { picture: { x: 1, y: 2, w: 3, h: 4 } }, { revert: {
    after: "", parent: "sheet-1", position: "a0", props: { picture: { x: 1, y: 2, w: 3, h: 4 } } } })).surface, "sheet-1");
  // a drawing on a page of paper keeps its strokes in its file: its row marks it alone
  assert.equal(objectFrame(made("draw", { ink_url: "/api/uploads/i.ink" })), null);
  assert.equal(objectFrame(edit("b", "a", "b")), null);
});

test("a move and the edit after it are one change, and Keep All keeps both", async () => {
  const asked = server((path, body) => (path.startsWith("/ai/changes?page_id=")
    ? { changes: [row("m1", move("b"), { at: "2026-11-25T10:00:01Z" }), row("e1", edit("b", "one", "two"), { at: "2026-11-25T10:00:02Z" })] }
    : path === "/ai/changes/keep" ? { kept: body.ids } : {}));
  await noteReview.load("p");
  assert.deepEqual(snap().changes.get("b").ids, ["m1", "e1"]);
  await noteReview.keepPage("p");
  assert.deepEqual(asked.at(-1), ["/ai/changes/keep", { ids: ["m1", "e1"] }]);
  assert.equal(snap().changes.get("b").state, "kept");
  noteReview.drop("p");
});

test("Keep takes the journal's word: a change undone elsewhere meanwhile is never taken as kept", async () => {
  const made = { g1: edit("a", "1", "2"), g2: edit("b", "3", "4"), g3: edit("c", "5", "6") };
  const journal = new Map(Object.keys(made).map((id) => [id, "open"]));
  const asked = server((path, body) => {
    if (path.startsWith("/ai/changes?page_id=")) {
      return { changes: [...journal].filter(([, state]) => state !== "undone").map(([id, state]) => row(id, made[id], { state })) };
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
  await noteReview.keepBlock("a");
  assert.deepEqual([snap().states.get("g1"), snap().changes.has("a")], ["undone", false], "one: the answer's state");
  await noteReview.keep(["g2", "g3"]);
  await flush();
  assert.deepEqual([snap().states.get("g2"), snap().changes.get("b")?.state], ["kept", "kept"]);
  assert.notEqual(snap().states.get("g3"), "kept", "the batch: only the ids it kept");
  assert.equal(snap().changes.has("c"), false, "its page read again: undone, it is not drawn");
  assert.equal(pageReads(asked).length, 2);
  unwatch();
  await flush();
});

test("a Keep that fails says why on the block's bar, as an Undo's refusal does", async () => {
  server((path) => {
    if (path.startsWith("/ai/changes?page_id=")) return { changes: [row("f1", edit("a", "1", "2"))] };
    throw new Error("The server is away.");
  });
  await noteReview.load("p");
  const why = { text: "The server is away.", force: "", diff: null };
  assert.deepEqual(await noteReview.keepBlock("a"), why);
  assert.deepEqual([snap().refusals.get("a"), snap().changes.get("a").state], [why, "open"]);
  noteReview.clearRefusal("a");
  noteReview.drop("p");
});

test("a block unfolded when its change goes starts the next one folded, and that mark's time runs", async () => {
  const clock = handClock();
  try {
    let rows = [{ ...row("x1", edit("a", "1", "2"), { state: "kept" }), kept_ago: 0 }];
    server((path) => (path.startsWith("/ai/changes?page_id=") ? { changes: rows } : { state: "undone" }));
    await noteReview.load("p");
    noteReview.toggle("a", true);
    assert.equal(await noteReview.undoBlock("a"), null);
    assert.equal(snap().expanded.has("a"), false, "undone: nothing is left unfolded");
    // the agent edits it again, allowed on its card
    rows = [{ ...row("x2", edit("a", "1", "3"), { state: "kept" }), kept_ago: 0 }];
    await noteReview.load("p");
    assert.deepEqual([snap().changes.get("a")?.state, snap().expanded.has("a")], ["kept", false], "a folded mark");
    clock.tick(61_000);
    assert.equal(snap().changes.has("a"), false, "its time is up");
  } finally {
    realClock();
    noteReview.drop("p");
  }
});

test("the row of an undone change unfolds nothing in its note; one redone elsewhere unfolds", async () => {
  server(() => ({ state: "undone" }));
  await noteReview.decide("u9", "undo");
  noteReview.reveal(edit("x", "a", "b", { change_id: "u9", reverted: true }), { undo: async () => {} });
  noteReview.reveal(edit("y", "a", "b", { reverted: true }), { undo: async () => {} });
  assert.deepEqual([snap().changes.has("x"), snap().expanded.has("x"), snap().changes.has("y")], [false, false, false]);
  // the conversation says reverted, the journal kept (redone in another tab): it stands
  noteReview.landed({ tool: "rename_page", change_id: "r9", approval: "once" });
  noteReview.reveal(edit("z", "a", "b", { change_id: "r9", reverted: true }), { undo: async () => {} });
  assert.deepEqual([snap().changes.get("z")?.state, snap().expanded.has("z")], ["kept", true]);
  noteReview.focus("");
  assert.equal(snap().changes.has("z"), false);
});

test("a change unfolded from a reply's row goes as it folds, the journal's once its time is up", async () => {
  const clock = handClock();
  try {
    noteReview.reveal(edit("old", "x", "y"), { undo: async () => {} });
    assert.equal(snap().changes.has("old"), true);
    noteReview.toggle("old", false);
    assert.equal(snap().changes.has("old"), false, "folded: gone");
    // unfolded from its row before its page was read, then read near the end of its mark's time
    server((path) => (path.startsWith("/ai/changes?page_id=")
      ? { changes: [{ ...row("j1", edit("b", "1", "2"), { state: "kept" }), kept_ago: 55 }] } : {}));
    noteReview.reveal(edit("b", "1", "2", { change_id: "j1" }), { undo: async () => {} });
    await noteReview.load("p");
    noteReview.toggle("b", false);
    assert.equal(snap().changes.has("b"), true, "folded within its time: its mark");
    clock.tick(6_000);
    assert.equal(snap().changes.has("b"), false, "folded, its time ran out");
  } finally {
    realClock();
    noteReview.drop("p");
  }
});

test("a page is read once while things on screen draw it, and dropped once the last goes", async () => {
  const asked = onPage([row("w1", edit("a", "1", "2"))]);
  const strip = noteReview.watch("p");
  const pdfPage = noteReview.watch("p");
  await flush();
  assert.equal(pageReads(asked).length, 1, "the second reads nothing more");
  assert.equal(snap().changes.has("a"), true);
  strip();
  strip(); // a second call is nothing
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

test("the server's nudge reads again a page on screen alone; a new mark time reads them again", async () => {
  const asked = onPage([]);
  globalThis.window = new EventTarget();
  const nudge = (pageId) => globalThis.window.dispatchEvent(new CustomEvent("gamma:ai-changes", { detail: { pageId } }));
  try {
    const unwatch = noteReview.watch("p");
    await flush();
    nudge("p");
    nudge("elsewhere");
    await flush();
    assert.deepEqual(pageReads(asked), ["/ai/changes?page_id=p&marks_for=60", "/ai/changes?page_id=p&marks_for=60"]);
    noteReview.setMarkLife(0);
    await flush();
    assert.equal(pageReads(asked).at(-1), "/ai/changes?page_id=p", "till cleared: every mark");
    noteReview.setMarkLife(60_000);
    await flush();
    unwatch();
    await flush();
    nudge("p");
    await flush();
    assert.equal(pageReads(asked).length, 4, "nothing on screen: no nudge is heard");
  } finally {
    delete globalThis.window;
  }
});

test("the newest answer about a page stands, whichever comes first; one sent before the page was dropped is left", async () => {
  const answers = [];
  noteReview.setRequest(() => new Promise((resolve) => answers.push(resolve)));
  const older = noteReview.load("p");
  const newer = noteReview.load("p");
  answers[1](served({ changes: [row("o1", edit("b", "1", "2"), { state: "kept" }), row("o2", edit("c", "3", "4"))] }));
  await newer;
  answers[0](served({ changes: [row("o1", edit("b", "1", "2"))] }));
  await older;
  assert.deepEqual([snap().changes.get("b")?.state, snap().changes.has("c")], ["kept", true]);
  const late = noteReview.load("p");
  noteReview.drop("p");
  answers[2](served({ changes: [row("o3", edit("d", "5", "6"))] }));
  await late;
  assert.deepEqual([snap().pages.has("p"), snap().changes.has("d")], [false, false], "dropped: the read on its way is left");
});

test("a state is not read again while its read is on its way, nor straight after a failed read", async () => {
  const clock = handClock();
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
    clock.tick(31_000);
    noteReview.want("fail1");
    await flush();
    assert.equal(asked.length, 3, "asked again once a while has passed");
    answer.resolve({ states: { fail1: "open" } });
    await flush();
    assert.equal(snap().states.get("fail1"), "open");
  } finally {
    realClock();
  }
});
