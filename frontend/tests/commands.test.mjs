// The command catalogs (app/commands.js) and what keeps them honest: unique
// ids, no two defaults on one chord within a scope, every default chord in
// the user guide's cheat sheet, and the pure helpers the block commands
// call (blockModel's sibling move / delete-keeping-children / neighbour,
// mdMarks' to-do toggle).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ALL_COMMANDS, GROUPS, commandById, commandChord, fixedKeys } from "../src/app/commands.js";
import { PREFS, readProfile } from "../src/app/prefDefs.js";
import { APP_COMMANDS } from "../src/app/appCommands.js";
import { BLOCK_COMMANDS } from "../src/editor/blockCommands.js";
import { chordLabel, conflicts, effectiveKeys, normalizeChord } from "../src/shared/lib/hotkeys.js";
import {
  EMPTY_VIEW, addSiblingBlock, makeNewBlock, mergeIntoAbove, moveSibling, removeBlockKeepChildren, visibleNeighbor,
} from "../src/shared/model/blockModel.js";
import { newBlockKey } from "../src/editor/blockCommands.js";
import { toggleTodoLine } from "../src/editor/mdMarks.js";

const GUIDE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../../docs/user_guide/user_guide.md");

test("every command has a unique id, a label, a known group and canonical default keys", () => {
  const ids = new Set();
  for (const cmd of ALL_COMMANDS) {
    assert.ok(cmd.id && !ids.has(cmd.id), `duplicate or missing id: ${cmd.id}`);
    ids.add(cmd.id);
    assert.ok(cmd.label, `${cmd.id} has no label`);
    assert.ok(GROUPS.includes(cmd.group), `${cmd.id} is in an unknown group "${cmd.group}"`);
    assert.equal(typeof cmd.run, "function", `${cmd.id} has no run`);
    for (const k of effectiveKeys(cmd, {})) assert.equal(k, normalizeChord(k), `${cmd.id}: "${k}" is not canonical`);
  }
  const byId = (id) => ALL_COMMANDS.find((c) => c.id === id);
  assert.equal(byId("block.deleteLine")?.keys, "Mod-Shift-k");
  assert.equal(byId("app.renameTitle")?.keys, "F2");
});

test("no two defaults share a chord within a scope", () => {
  for (const [name, list] of [["app", APP_COMMANDS], ["block", BLOCK_COMMANDS]]) {
    const clash = conflicts(list, {});
    assert.equal(clash.size, 0, `${name}: ${[...clash.entries()].map(([k, v]) => `${k} → ${v.map((c) => c.id).join(", ")}`).join("; ")}`);
  }
});

test("the user guide's cheat sheet names every default shortcut", () => {
  const guide = fs.readFileSync(GUIDE, "utf8");
  const sheet = guide.slice(guide.indexOf("## Shortcut cheat sheet"));
  assert.ok(sheet.length > 100, "the cheat sheet section exists");
  for (const cmd of ALL_COMMANDS) {
    for (const k of effectiveKeys(cmd, {})) {
      const label = chordLabel(k, false);
      assert.ok(sheet.includes(label), `${cmd.id}: "${label}" is missing from the cheat sheet`);
    }
  }
  for (const [chords] of fixedKeys(false)) {
    for (const c of chords) assert.ok(sheet.includes(chordLabel(c, false)), `built-in "${chordLabel(c, false)}" is missing from the cheat sheet`);
  }
});

test("the handwriting commands keep to the strip's selection and clipboard, and decline a key from a text field", () => {
  const by = (id) => commandById(id);
  assert.deepEqual(["ink.copy", "ink.cut", "ink.paste", "ink.duplicate", "ink.selectAll"].map((id) => by(id).keys),
    ["Mod-c", "Mod-x", "Mod-v", "Mod-d", "Mod-a"]);
  assert.ok(["ink.copy", "ink.cut", "ink.paste", "ink.duplicate", "ink.selectAll"].every((id) => by(id).edits && GROUPS.includes(by(id).group)));
  const none = { inkOpen: false, inkTool: false, inkSelected: false, inkClip: false };
  for (const id of ["ink.copy", "ink.cut", "ink.duplicate"]) {
    assert.equal(by(id).when(none), false, `${id} waits for a selection`);
    assert.equal(by(id).when({ ...none, inkSelected: true }), true);
  }
  assert.equal(by("ink.paste").when({ ...none, inkOpen: true, inkTool: true }), false, "paste waits for a clipboard");
  assert.equal(by("ink.paste").when({ ...none, inkOpen: true, inkTool: true, inkClip: true }), true, "outside a browser no text is selected");
  assert.equal(by("ink.paste").when({ ...none, inkOpen: true, inkClip: true }), false, "paste needs a tool other than the hand");
  assert.equal(by("ink.selectAll").when({ ...none, inkOpen: true, inkTool: true }), true);
  assert.equal(by("ink.selectAll").when({ ...none, inkOpen: true }), false);
  const calls = [];
  const ctx = { inkCopy: () => { calls.push("copy"); return true; }, inkSelectAll: () => { calls.push("all"); return true; } };
  assert.equal(by("ink.copy").run(ctx, { target: { tagName: "INPUT" } }), false, "a key in a text field is declined");
  assert.equal(by("ink.copy").run(ctx, { target: { tagName: "DIV", isContentEditable: false } }), true);
  assert.equal(by("ink.selectAll").run(ctx), true, "no event from the palette or a menu");
  assert.deepEqual(calls, ["copy", "all"]);
});

test("the PDF's held keys are lone modifiers that never dispatch", () => {
  assert.equal(commandChord("pdf.areaDrag", {}), "Mod", "Ctrl, ⌘ on a Mac");
  assert.equal(commandChord("pdf.peekOriginal", {}), "Alt");
  assert.equal(commandChord("pdf.areaDrag", { "pdf.areaDrag": "Alt" }), "Alt", "rebound in Settings → Keyboard");
  for (const id of ["pdf.areaDrag", "pdf.peekOriginal"]) {
    const cmd = commandById(id);
    assert.ok(cmd.held && cmd.palette === false, `${id} is held and kept out of the palette`);
    assert.equal(cmd.run(), false, `${id} declines if it is ever run`);
  }
});

test("fixedKeys follows the Enter preference", () => {
  assert.deepEqual(fixedKeys(false)[0][0], ["Shift-Enter"]);
  assert.deepEqual(fixedKeys(true)[0][0], ["Enter"]);
  assert.deepEqual(fixedKeys(true)[1][0], ["Shift-Enter"]);
  assert.equal(PREFS.enterNewNote.default, true, "Enter makes a new note unless the account says otherwise");
  assert.equal(readProfile({ enterNewNote: false }).enterNewNote, false, "a stored choice stays");
});

test("fixedKeys names all that Backspace does", () => {
  const [, what] = fixedKeys(false).find(([, , id]) => id === "backspace");
  for (const word of ["empty note", "list marker", "note above"]) assert.ok(what.includes(word), word);
});

test("commandChord is the chord a command answers to for this account", () => {
  assert.equal(commandChord("app.quickOpen", {}), "Mod-p");
  assert.equal(commandChord("app.redo", {}), "Mod-y", "the first of several defaults");
  assert.equal(commandChord("app.quickOpen", { "app.quickOpen": "Mod-Shift-o" }), "Mod-Shift-o", "a rebound command");
  assert.equal(commandChord("app.quickOpen", { "app.quickOpen": null }), "", "an unbound command");
  assert.equal(commandChord("app.share", {}), "", "a command with no default");
  assert.equal(commandChord("app.nothing", {}), "");
  assert.equal(commandById("app.share").label, "Share this page…");
  assert.equal(commandById("app.nothing"), null);
});

const tree = () => [
  { id: "a", content: "a", children: [{ id: "a1", content: "a1", children: [] }, { id: "a2", content: "a2", properties: { collapsed: true }, children: [{ id: "a2x", content: "x", children: [] }] }] },
  { id: "b", content: "b", children: [] },
];
const ids = (list) => list.map((b) => b.id + (b.children?.length ? `(${ids(b.children)})` : "")).join(",");

test("moveSibling swaps with the neighbour and stays put at the edges", () => {
  const t = tree();
  assert.equal(ids(moveSibling(t, "a2", -1)), "a(a2(a2x),a1),b");
  assert.equal(ids(moveSibling(t, "a", 1)), "b,a(a1,a2(a2x))");
  assert.equal(moveSibling(t, "b", 1), t, "same reference at the bottom");
  assert.equal(moveSibling(t, "a1", -1), t, "same reference at the top of its parent");
  assert.equal(moveSibling(t, "zzz", 1), t, "same reference for an unknown id");
});

test("removeBlockKeepChildren lifts the children into the block's place", () => {
  assert.equal(ids(removeBlockKeepChildren(tree(), "a")), "a1,a2(a2x),b");
  assert.equal(ids(removeBlockKeepChildren(tree(), "a2")), "a(a1,a2x),b");
  assert.equal(ids(removeBlockKeepChildren(tree(), "b")), "a(a1,a2(a2x))");
});

test("addSiblingBlock makes the block and keeps this one's text in one tree", () => {
  const P = (id, content, children = []) => ({ id, content, children, properties: {} });
  const list = () => [P("a", "a", [P("a1", "a1")]), P("b", "b")];
  const shown = (t) => JSON.stringify(t.map(function line(b) { return [b.content, ...(b.children || []).map(line)]; }));
  const below = addSiblingBlock(list(), "a");
  assert.equal(shown(below.blocks), '[["a",["a1"]],[""],["b"]]', "empty, right below, its subtree between");
  assert.ok(below.newId);
  assert.equal(shown(addSiblingBlock(list(), "b", { above: true }).blocks), '[["a",["a1"]],[""],["b"]]', "above");
  assert.equal(shown(addSiblingBlock(list(), "b", { content: "x" }).blocks), '[["a",["a1"]],["b"],["x"]]', "with text");
  assert.equal(shown(addSiblingBlock(list(), "a", { keep: "kept", content: "x", firstChild: true }).blocks),
    '[["kept",["x"],["a1"]],["b"]]', "first under the children, this block's text in the same tree");
  assert.equal(shown(addSiblingBlock(list(), "b", { keep: "", content: "x" }).blocks), '[["a",["a1"]],[""],["x"]]', "an empty text is a text");
  const own = makeNewBlock({ content: "mine" });
  const made = addSiblingBlock(list(), "a", { newBlock: own });
  assert.equal(made.newId, own.id, "a block made beforehand keeps its id");
  assert.equal(made.blocks[1], own);
});

test("mergeIntoAbove joins a block to the one shown above, marker gone, caret at the seam", () => {
  const P = (id, content, children = [], properties = {}) => ({ id, content, children, properties });
  const shown = (t) => JSON.stringify(t.map(function line(b) { return [b.content, ...(b.children || []).map(line)]; }));
  const merge = (t, id, view = EMPTY_VIEW) => mergeIntoAbove(t, id, view);

  let m = merge([P("a", "one"), P("b", "two")], "b");
  assert.equal(shown(m.blocks), '[["onetwo"]]');
  assert.deepEqual([m.gone, m.focusId, m.caret], ["b", "a", 3]);
  assert.equal(shown(merge([P("a", "one"), P("b", "- two\n- three")], "b").blocks), '[["onetwo\\n- three"]]', "only the first line's marker");
  assert.equal(shown(merge([P("a", "one"), P("b", "## two")], "b").blocks), '[["onetwo"]]', "a heading's");
  assert.equal(shown(merge([P("a", "one"), P("b", "- [x] two")], "b").blocks), '[["onetwo"]]', "a to-do's");
  assert.equal(shown(merge([P("a", "- one"), P("b", "- two")], "b").blocks), '[["- onetwo"]]', "the block above keeps its own");
  assert.equal(merge([P("a", "x", [], { collapsed: false, t0: 5 }), P("b", "y", [], { t0: 6 })], "b").blocks[0].properties.t0, 5, "fold and t0 are no content");

  // Children keep their reading order.
  m = merge([P("a", "one"), P("b", "two", [P("c", "kid")])], "b");
  assert.equal(shown(m.blocks), '[["onetwo",["kid"]]]', "they go under the block above");
  m = merge([P("p", "parent", [P("b", "two", [P("c", "kid")]), P("d", "next")])], "b");
  assert.equal(shown(m.blocks), '[["parenttwo",["kid"],["next"]]]', "into its parent: spliced in at its place");
  assert.equal(m.focusId, "p");
  m = merge([P("a", "one", [P("x", "deep")]), P("b", "two", [P("c", "kid")])], "b");
  assert.equal(shown(m.blocks), '[["one",["deeptwo",["kid"]]]]', "the block shown above may sit deeper");
  m = merge([P("a", "one", [P("x", "hidden")], { collapsed: true }), P("b", "two")], "b", { editingId: null, folds: { a: false } });
  assert.equal(shown(m.blocks), '[["one",["hiddentwo"]]]', "the viewer's own fold decides what is shown");

  // An empty block above goes, and this one stays as it is.
  m = merge([P("a", ""), P("b", "- two", [P("c", "kid")])], "b");
  assert.equal(shown(m.blocks), '[["- two",["kid"]]]');
  assert.deepEqual([m.gone, m.focusId, m.caret], ["a", "b", 0]);
  m = merge([P("p", " ", [P("b", "two"), P("d", "next")])], "b");
  assert.equal(shown(m.blocks), '[["two"],["next"]]', "an empty parent lets go of its children, not this one too");
  assert.equal(shown(merge([P("a", ""), P("b", "> quote")], "b").blocks), '[["> quote"]]', "nothing is joined, so any first line will do");
  assert.equal(shown(merge([P("a", ""), P("b", "```\nx\n```")], "b").blocks), '[["```\\nx\\n```"]]');
  m = merge([P("a", "", [], { collapsed: true }), P("b", "two", [P("c", "kid")])], "b");
  assert.equal(shown(m.blocks), '[["two",["kid"]]]', "a leftover fold on the empty one hides nothing once it goes");
  assert.equal(m.gone, "a");
  m = merge([P("a", ""), P("b", "two", [P("c", "kid")])], "b", { editingId: null, folds: { a: true } });
  assert.equal(shown(m.blocks), '[["two",["kid"]]]', "nor does this viewer's");
});

test("mergeIntoAbove refuses what a join would break", () => {
  const P = (id, content, children = [], properties = {}) => ({ id, content, children, properties });
  const refuses = (above, content, what) => assert.equal(mergeIntoAbove([P("a", above), P("b", content)], "b", EMPTY_VIEW), null, what);

  assert.equal(mergeIntoAbove([P("b", "first")], "b", EMPTY_VIEW), null, "nothing above");
  assert.equal(mergeIntoAbove([P("a", "x")], "gone", EMPTY_VIEW), null, "an unknown block");
  // Either block more than a plain note.
  const properties = [
    { pdf_position: { pageNumber: 1 } }, { text_box: {} }, { picture: {} }, { ink_url: "" }, { sheet: {} }, { audio_url: "x" },
    { zotero_note: "k" }, { zotero_attachments: [] }, { imported_annot: true }, { linked_highlight_id: "h" }, { link_url: "u" },
  ];
  for (const p of properties) {
    assert.equal(mergeIntoAbove([P("a", "x", [], p), P("b", "y")], "b", EMPTY_VIEW), null, `above: ${Object.keys(p)}`);
    assert.equal(mergeIntoAbove([P("a", "x"), P("b", "y", [], p)], "b", EMPTY_VIEW), null, `this block: ${Object.keys(p)}`);
    assert.equal(mergeIntoAbove([P("a", "", [], p), P("b", "y")], "b", EMPTY_VIEW), null, `an empty one above: ${Object.keys(p)}`);
  }
  // The block above hides children, or would hide this block's.
  assert.equal(mergeIntoAbove([P("a", "x", [P("k", "kid")], { collapsed: true }), P("b", "y")], "b", EMPTY_VIEW), null, "folded for everyone");
  assert.equal(mergeIntoAbove([P("a", "x", [P("k", "kid")]), P("b", "y")], "b", { editingId: null, folds: { a: true } }), null, "folded here");
  assert.equal(mergeIntoAbove([P("a", "", [P("k", "kid")], { collapsed: true }), P("b", "y")], "b", EMPTY_VIEW), null, "folded and empty");
  assert.equal(mergeIntoAbove([P("a", "x", [], { collapsed: true }), P("b", "y", [P("k", "kid")])], "b", EMPTY_VIEW), null, "a fold with nothing in it yet");
  assert.ok(mergeIntoAbove([P("a", "x", [], { collapsed: true }), P("b", "y")], "b", EMPTY_VIEW), "a fold with nothing to hide is no fold");
  // The last line above is no text to join.
  refuses("```\ncode\n```", "y", "a closed fence");
  refuses("```\ncode", "y", "a fence still open");
  refuses("$$\nx", "y", "display math still open");
  refuses("text $$\nx", "y", "display math opened mid-line");
  refuses("$$\nx\n$$", "y", "display math");
  refuses("$$x$$", "y", "display math on a line");
  refuses("| a |\n| - |\n| b |", "y", "a table");
  refuses("---", "y", "a divider");
  refuses("text\n---", "y", "a divider under text");
  assert.ok(mergeIntoAbove([P("a", "```\ncode\n```\nafter"), P("b", "y")], "b", EMPTY_VIEW), "text after a fence is text");
  assert.ok(mergeIntoAbove([P("a", "$$\nx\n$$\nafter"), P("b", "y")], "b", EMPTY_VIEW), "text after display math is text");
  assert.ok(mergeIntoAbove([P("a", "cost $5"), P("b", "y")], "b", EMPTY_VIEW), "only display math refuses");
  // This block's first line is no text to join.
  refuses("x", "```\ncode\n```", "a fence");
  refuses("x", "$$\ny\n$$", "display math");
  refuses("x", "| a |\n| - |", "a table");
  refuses("x", "---", "a divider");
  refuses("x", "> quote", "a quote");
  refuses("x", "> [!tip] title", "a callout");
  assert.ok(mergeIntoAbove([P("a", "x"), P("b", "y\n> quote")], "b", EMPTY_VIEW), "only the first line joins");
});

test("block.newBelow is the new-block key: from the open editor, or an empty sibling with none", () => {
  const cmd = BLOCK_COMMANDS.find((c) => c.id === "block.newBelow");
  const press = (doc, from, to = from, { block = { id: "a", properties: {} }, editor = true } = {}) => {
    const calls = [];
    const view = { state: { doc: { toString: () => doc }, selection: { main: { from, to } } } };
    const row = { onNewBlock: (id, plan) => calls.push(["new", id, plan]), onEnterSibling: (id) => calls.push(["sibling", id]) };
    cmd.run({ block, row, editor: editor ? { view } : null });
    return calls[0];
  };
  assert.deepEqual(press("ab", 1), ["new", "a", { keep: "a", next: "b", caret: 0 }]);
  assert.deepEqual(press("- ab", 4), ["new", "a", { keep: "- ab", next: "- ", caret: 2 }]);
  assert.deepEqual(press("abcd", 1, 3), ["new", "a", { keep: "a", next: "d", caret: 0 }], "over a selection");
  assert.deepEqual(press("ab", 1, 1, { editor: false }), ["sibling", "a"], "no editor: no needsEditor, so c.editor may be missing");
  assert.deepEqual(press("ab", 1, 1, { block: { id: "a", properties: { text_box: {} } } }), ["sibling", "a"], "a text box never splits");
  assert.deepEqual(press("```\ncode\n```", 6), ["sibling", "a"], "nor does a fence");
  assert.deepEqual(press("> ```\n> code\n> ```", 10), ["sibling", "a"], "a quoted one");
  assert.deepEqual(press("> [!note] t\n> ```\n> code\n> ```", 21), ["sibling", "a"], "one in a callout");
  assert.deepEqual(press("~~~\ncode\n~~~", 6), ["sibling", "a"], "or a tilde one");
  assert.deepEqual(press("$$\nx\n$$", 3), ["sibling", "a"], "nor display math");
  assert.deepEqual(press("see $ab$ here", 6), ["new", "a", { keep: "see $ab$", next: "here", caret: 0 }], "inline math stays whole");
  assert.equal(cmd.needsEditor, undefined);
});

test("newBlockKey says whether a block was made", () => {
  const key = (doc) => newBlockKey({
    block: { id: "a", properties: {} },
    row: { onNewBlock() {}, onEnterSibling() {} },
    editor: { view: { state: { doc: { toString: () => doc }, selection: { main: { from: doc.length, to: doc.length } } } } },
  });
  assert.equal(key("- a"), true);
  assert.equal(key("- "), false, "an empty item only ends");
});

test("visibleNeighbor walks the outliner as shown, folded subtrees skipped", () => {
  const t = tree();
  assert.equal(visibleNeighbor(t, "a", 1, EMPTY_VIEW)?.id, "a1");
  assert.equal(visibleNeighbor(t, "a2", 1, EMPTY_VIEW)?.id, "b", "a2 is folded: its child is not shown");
  assert.equal(visibleNeighbor(t, "b", -1, EMPTY_VIEW)?.id, "a2");
  assert.equal(visibleNeighbor(t, "a", -1, EMPTY_VIEW), null);
  assert.equal(visibleNeighbor(t, "b", 1, EMPTY_VIEW), null);
});

test("toggleTodoLine adds, checks and unchecks a checkbox on the caret's line", () => {
  assert.equal(toggleTodoLine("hello", 3).text, "- [ ] hello");
  assert.equal(toggleTodoLine("- [ ] task", 8).text, "- [x] task");
  assert.equal(toggleTodoLine("- [x] task", 8).text, "- [ ] task");
  assert.equal(toggleTodoLine("  - item", 5).text, "  - [ ] item");
  assert.equal(toggleTodoLine("1. step", 3).text, "1. [ ] step");
  const r = toggleTodoLine("a\n- [ ] b\nc", 4);
  assert.equal(r.text, "a\n- [x] b\nc");
  assert.deepEqual([r.from, r.to, r.insert], [2, 9, "- [x] b"]);
  // The caret moves with the inserted marker, never before its line.
  assert.equal(toggleTodoLine("hello", 0).pos, 6);
  assert.equal(toggleTodoLine("hello", 5).pos, 11);
});
