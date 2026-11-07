// The touch editing bar's own pieces (editor/EditBar.jsx runs them through
// the block catalog): the "/" and "$…$" inserts a tap makes, and where the
// catalog says a block can be moved, indented or outdented — the bar greys
// a button out when its command's `when` fails.
import assert from "node:assert/strict";
import { test } from "node:test";
import { EditorSelection, EditorState } from "@codemirror/state";
import { mathInsertAt, slashInsertAt } from "../src/editor/markCommands.js";
import { BLOCK_COMMANDS } from "../src/editor/blockCommands.js";

function apply(text, r) {
  let out = text;
  for (const c of [...r.changes].sort((a, b) => b.from - a.from)) out = out.slice(0, c.from) + c.insert + out.slice(c.to);
  return { text: out, sel: r.selection };
}

test("Insert puts a / where the slash menu opens on it", () => {
  assert.deepEqual(apply("", slashInsertAt("", 0, 0)), { text: "/", sel: { anchor: 1 } });
  // after a word it needs a space, as the typed trigger does
  assert.deepEqual(apply("note", slashInsertAt("note", 4, 4)), { text: "note /", sel: { anchor: 6 } });
  assert.deepEqual(apply("a b", slashInsertAt("a b", 2, 2)), { text: "a /b", sel: { anchor: 3 } });
  // a selection is kept as text: the / goes after it
  assert.deepEqual(apply("word", slashInsertAt("word", 0, 4)), { text: "word /", sel: { anchor: 6 } });
});

test("Insert does nothing inside math or a code fence", () => {
  assert.equal(slashInsertAt("$x+y$", 2, 2), null);
  assert.equal(slashInsertAt("```\ncode\n```", 6, 6), null);
  // at the edge of a span the caret is outside it
  assert.deepEqual(apply("$x$", slashInsertAt("$x$", 3, 3)).text, "$x$ /");
});

test("Math wraps the selection or inserts $x$ with the x selected", () => {
  assert.deepEqual(apply("a", mathInsertAt("a", 1, 1)), { text: "a$x$", sel: { anchor: 2, head: 3 } });
  assert.deepEqual(apply("let E=mc^2 hold", mathInsertAt("let E=mc^2 hold", 4, 10)),
    { text: "let $E=mc^2$ hold", sel: { anchor: 5, head: 11 } });
  assert.equal(mathInsertAt("two\nlines", 0, 9), null);
  assert.equal(mathInsertAt("$x$", 1, 2), null);
  assert.equal(mathInsertAt("```\ncode\n```", 5, 7), null);
});

test("moving and nesting a block apply only where it can go", () => {
  const cmd = (id) => BLOCK_COMMANDS.find((c) => c.id === id);
  const tree = [
    { id: "a", children: [{ id: "a1", children: [] }, { id: "a2", children: [] }] },
    { id: "b", children: [] },
  ];
  const can = (id, block) => cmd(id).when({ tree, block: { id: block } });
  assert.deepEqual(["a", "b", "a1", "a2"].map((b) => can("block.moveUp", b)), [false, true, false, true]);
  assert.deepEqual(["a", "b", "a1", "a2"].map((b) => can("block.moveDown", b)), [true, false, true, false]);
  assert.deepEqual(["a", "b", "a1", "a2"].map((b) => can("block.indent", b)), [false, true, false, true]);
  assert.deepEqual(["a", "b", "a1", "a2"].map((b) => can("block.outdent", b)), [false, false, true, true]);
});

test("next math argument is Tab's hop, offered only where Tab would hop", () => {
  const cmd = BLOCK_COMMANDS.find((c) => c.id === "block.nextSlot");
  const editor = (value, at) => {
    const ed = { value, selectionStart: at, selectionEnd: at, setSelectionRange(a, h) { ed.sel = [a, h]; } };
    return ed;
  };
  const hop = (value, at) => {
    const ed = editor(value, at);
    if (!cmd.when({ editor: ed })) return null;
    cmd.run({ editor: ed });
    return ed.sel;
  };
  const doc = "$\\frac{a}{bc}$ x";
  assert.deepEqual(hop(doc, 7), [10, 12], "numerator → the denominator, selected");
  assert.deepEqual(hop(doc, 12), [13, 13], "past the closing brace");
  assert.deepEqual(hop(doc, 13), [14, 14], "out of the span");
  assert.equal(hop(doc, 15), null, "in prose there is no slot");
  assert.equal(hop("```\n$\\frac{a}{b}$\n```", 11), null, "a $ in a code fence is no math");
  assert.equal(cmd.when({ editor: null }), false);
});

test("Line break is the row's line-break key: a list goes on, code and math take a plain new line", () => {
  const cmd = BLOCK_COMMANDS.find((c) => c.id === "block.lineBreak");
  assert.equal(cmd.needsEditor, true);
  // The command on a real editor state, for the fall through to CodeMirror.
  const broken = (text, from, to = from) => {
    const view = {
      state: EditorState.create({ doc: text, selection: EditorSelection.range(from, to) }),
      dispatch(tr) { view.state = (tr.startState ? tr : view.state.update(tr)).state; },
    };
    assert.equal(cmd.run({ editor: { view } }), true);
    const out = view.state.doc.toString();
    return [out, out.slice(0, view.state.selection.main.head)];
  };
  assert.deepEqual(broken("plain", 2), ["pl\nain", "pl\n"]);
  assert.deepEqual(broken("- a", 3), ["- a\n- ", "- a\n- "], "the list goes on, in this block");
  assert.deepEqual(broken("> a", 3), ["> a\n> ", "> a\n> "], "and a quote");
  assert.deepEqual(broken("- ", 2), ["", ""], "an empty item ends");
  assert.deepEqual(broken("plain", 1, 4), ["p\nn", "p\n"], "a selection goes");
  assert.deepEqual(broken("```\n- code\n```", 10), ["```\n- code\n\n```", "```\n- code\n"], "no list in a fence");
  assert.deepEqual(broken("$$\n- x\n$$", 6), ["$$\n- x\n\n$$", "$$\n- x\n"], "nor in display math");
  assert.deepEqual(broken("> $$\n> x\n> $$", 8), ["> $$\n> x\n> \n> $$", "> $$\n> x\n> "], "math in a quote keeps its marks");

  assert.deepEqual(broken("> ```\n> ab\n> ```", 10), ["> ```\n> ab\n> \n> ```", "> ```\n> ab\n> "], "a quoted fence keeps its marks");
  assert.deepEqual(broken("> ```\n> - ab\n> ```", 12), ["> ```\n> - ab\n> \n> ```", "> ```\n> - ab\n> "], "and goes on with no list");
  assert.deepEqual(broken("~~~\n- a\n~~~", 7), ["~~~\n- a\n\n~~~", "~~~\n- a\n"], "no list in a tilde fence");

  // With no marker to go on, the key is CodeMirror's Enter.
  assert.deepEqual(broken("- a\n  cont", 10), ["- a\n  cont\n  ", "- a\n  cont\n  "], "an item's continuation keeps its indent");
  assert.deepEqual(broken("    indented", 12), ["    indented\n    ", "    indented\n    "], "and an indented line");
  assert.deepEqual(broken("ab   cd", 2), ["ab\ncd", "ab\n"], "the spaces at the caret go");
  assert.deepEqual(broken("  word", 2, 6), ["\n  ", "\n  "], "a selection after the indent starts its line over");

  // A fence comes first: what looks like math in one is code.
  assert.deepEqual(broken("```\n$$\n> x\n$$\n```", 10), ["```\n$$\n> x\n\n$$\n```", "```\n$$\n> x\n"], "no quote marks in a fence");

  let sent;
  const view = {
    state: { doc: { toString: () => "- a" }, selection: { main: { from: 3, to: 3 } } },
    dispatch: (spec) => { sent = spec; },
  };
  assert.equal(cmd.run({ editor: { view } }), true);
  assert.deepEqual(sent, { changes: { from: 3, to: 3, insert: "\n- " }, selection: { anchor: 6 }, scrollIntoView: true, userEvent: "input" });
});

test("the bar's new commands are in the catalog, unbound", () => {
  for (const id of ["block.newBelow", "block.lineBreak", "block.insertMenu", "block.math", "block.nextSlot"]) {
    const c = BLOCK_COMMANDS.find((x) => x.id === id);
    assert.ok(c, id);
    assert.equal(c.keys, null, `${id} starts unbound`);
  }
});
