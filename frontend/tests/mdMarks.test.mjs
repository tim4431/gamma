// The formatting hotkeys' pure toggle (Ctrl+B / I / U / E / Shift+X /
// Shift+H) and the inline-mark scanner both the editor's live rendering and
// the toggle read. Each case applies the returned changes to the text and
// checks the result plus where the selection lands.
import assert from "node:assert/strict";
import { test } from "node:test";
import { expandBlankLines, insertLink, isUrl, markedAt, scanMarks, toggleMark } from "../src/editor/mdMarks.js";
import { BLOCK_COMMANDS } from "../src/editor/blockCommands.js";

// Apply {from, to, insert} changes addressed to the original text.
function apply(text, changes) {
  let out = text;
  for (const c of [...changes].sort((a, b) => b.from - a.from)) out = out.slice(0, c.from) + c.insert + out.slice(c.to);
  return out;
}
const toggled = (text, from, to, marker) => {
  const r = toggleMark(text, from, to, marker);
  return r && { text: apply(text, r.changes), sel: r.selection };
};

test("a selection is wrapped, with the inner text still selected", () => {
  assert.deepEqual(toggled("say word now", 4, 8, "**"), { text: "say **word** now", sel: { anchor: 6, head: 10 } });
  assert.deepEqual(toggled("say word now", 4, 8, "*"), { text: "say *word* now", sel: { anchor: 5, head: 9 } });
});

test("whitespace at the selection's edges stays outside the delimiters", () => {
  assert.deepEqual(toggled("say word now", 3, 9, "**"), { text: "say **word** now", sel: { anchor: 6, head: 10 } });
  assert.equal(toggleMark("a   b", 1, 4, "**"), null, "nothing but whitespace: nothing to do");
});

test("a caret inserts an empty pair; a second press removes it", () => {
  assert.deepEqual(toggled("word", 2, 2, "**"), { text: "wo****rd", sel: { anchor: 4 } });
  assert.deepEqual(toggled("wo****rd", 4, 4, "**"), { text: "word", sel: { anchor: 2 } });
});

test("a caret inside or right after a span unwraps it", () => {
  assert.deepEqual(toggled("**bold**", 4, 4, "**"), { text: "bold", sel: { anchor: 2, head: 2 } });
  assert.deepEqual(toggled("**bold** x", 8, 8, "**"), { text: "bold x", sel: { anchor: 4, head: 4 } });
  assert.deepEqual(toggled("**bold**", 2, 6, "**"), { text: "bold", sel: { anchor: 0, head: 4 } });
});

test("a multi-line selection wraps each non-blank line, or unwraps them all", () => {
  assert.deepEqual(toggled("a\n\nb", 0, 4, "**"), { text: "**a**\n\n**b**", sel: { anchor: 0, head: 12 } });
  assert.deepEqual(toggled("**a**\n**b**", 0, 11, "**"), { text: "a\nb", sel: { anchor: 0, head: 3 } });
  // Mixed: the wrapped line is left alone, the other gets wrapped.
  assert.deepEqual(toggled("**a**\nb", 0, 7, "**"), { text: "**a**\n**b**", sel: { anchor: 0, head: 11 } });
});

test("marks nest, and *** is one bold+italic span whose layers peel off separately", () => {
  assert.deepEqual(scanMarks("**a *b* c**").map((s) => [s.marker, s.from, s.to]), [["**", 0, 11], ["*", 4, 7]]);
  assert.deepEqual(scanMarks("*a **b** c*").map((s) => [s.marker, s.from, s.to]), [["*", 0, 11], ["**", 3, 8]]);
  const [triple] = scanMarks("***x***");
  assert.deepEqual([triple.marker, triple.layers], ["***", ["**", "*"]]);
  assert.deepEqual(toggled("***x***", 3, 4, "**"), { text: "*x*", sel: { anchor: 1, head: 2 } });
  assert.deepEqual(toggled("***x***", 3, 4, "*"), { text: "**x**", sel: { anchor: 2, head: 3 } });
});

test("nothing nests inside inline code, and claimed ranges are skipped", () => {
  assert.deepEqual(scanMarks("`**a**`").map((s) => s.marker), ["`"]);
  const claimed = [[0, 5]]; // e.g. a math span the caller already took
  assert.deepEqual(scanMarks("$**a**$ **b**", claimed).map((s) => [s.marker, s.from]), [["**", 8]]);
  assert.deepEqual(claimed, [[0, 5], [8, 13]], "accepted spans are pushed onto the claimed list");
});

test("openers and closers may not hug whitespace, and a span never crosses a line", () => {
  assert.deepEqual(scanMarks("** a**"), []);
  assert.deepEqual(scanMarks("**a\nb**"), []);
  assert.deepEqual(scanMarks("==hi== ~~x~~").map((s) => s.cls), ["cmHighlight", "cmStrike"]);
});

test("underline is the <u> tag pair, toggled like any mark", () => {
  assert.deepEqual(toggled("say word now", 4, 8, "<u>"), { text: "say <u>word</u> now", sel: { anchor: 7, head: 11 } });
  assert.deepEqual(toggled("say <u>word</u> now", 7, 11, "<u>"), { text: "say word now", sel: { anchor: 4, head: 8 } });
  assert.deepEqual(toggled("<u>word</u>", 5, 5, "<u>"), { text: "word", sel: { anchor: 2, head: 2 } });
  // A caret: an empty pair, and a second press takes it away.
  assert.deepEqual(toggled("a", 1, 1, "<u>"), { text: "a<u></u>", sel: { anchor: 4 } });
  assert.deepEqual(toggled("a<u></u>", 4, 4, "<u>"), { text: "a", sel: { anchor: 1 } });
  assert.deepEqual(toggled("a\nb", 0, 3, "<u>"), { text: "<u>a</u>\n<u>b</u>", sel: { anchor: 0, head: 17 } });
  assert.deepEqual(toggled("<u>a</u>\n<u>b</u>", 0, 17, "<u>"), { text: "a\nb", sel: { anchor: 0, head: 3 } });
  // Its tags may hug whitespace (HTML draws that), and it nests with the others.
  assert.deepEqual(scanMarks("<u> a </u>").map((s) => [s.marker, s.openLen, s.closeLen]), [["<u>", 3, 4]]);
  assert.deepEqual(scanMarks("**<u>x</u>**").map((s) => [s.marker, s.from, s.to]), [["**", 0, 12], ["<u>", 2, 10]]);
  assert.deepEqual(scanMarks("<u>**x**</u>").map((s) => [s.marker, s.from, s.to]), [["<u>", 0, 12], ["**", 3, 8]]);
  assert.deepEqual(toggled("**<u>x</u>**", 5, 6, "**"), { text: "<u>x</u>", sel: { anchor: 3, head: 4 } });
});

test("markedAt is what the toggle takes away", () => {
  assert.equal(markedAt("**bold** x", 2, 6, "**"), true);
  assert.equal(markedAt("**bold** x", 9, 10, "**"), false);
  assert.equal(markedAt("****", 2, 2, "**"), true, "a caret in an empty pair");
  assert.equal(markedAt("***x***", 3, 4, "*"), true, "one layer of ***");
  assert.equal(markedAt("**a**\nb", 0, 7, "**"), false, "a multi-line selection needs every line");
  assert.equal(markedAt("**a**\n**b**", 0, 11, "**"), true);
  assert.equal(markedAt("<u>x</u>", 3, 4, "<u>"), true);
});

test("a formatting command's state: on where the selection carries it, blocked where it does nothing", () => {
  const cmd = (id) => BLOCK_COMMANDS.find((c) => c.id === id);
  const state = (id, value, from, to) => cmd(id).state({ editor: { value, selectionStart: from, selectionEnd: to } });
  assert.equal(state("block.bold", "**b** x", 2, 3), "on");
  assert.equal(state("block.bold", "**b** x", 6, 7), "off");
  assert.equal(state("block.underline", "<u>b</u>", 3, 4), "on");
  assert.equal(state("block.bold", "$x+y$", 1, 3), "blocked", "math");
  assert.equal(state("block.bold", "a `code` b", 3, 6), "blocked", "inline code takes no other mark");
  assert.equal(state("block.code", "a `code` b", 3, 6), "on", "but code toggles off there");
  assert.equal(state("block.link", "say word", 4, 8), "off");
  assert.equal(state("block.link", "```\ncode\n```", 4, 8), "blocked");
  assert.equal(state("block.math", "two\nlines", 0, 9), "blocked");
  assert.equal(cmd("block.underline").keys, "Mod-u");
});

test("insertLink builds the link and parks the caret in the empty slot", () => {
  let r = insertLink("hello world", 0, 5, "http://x");
  assert.deepEqual([apply("hello world", r.changes), r.selection.anchor], ["[hello](http://x) world", 17]);
  r = insertLink("hello world", 0, 5);
  assert.deepEqual([apply("hello world", r.changes), r.selection.anchor], ["[hello]() world", 8]);
  r = insertLink("x", 1, 1);
  assert.deepEqual([apply("x", r.changes), r.selection.anchor], ["x[]()", 2]);
  assert.equal(isUrl(" https://a.b/c "), true);
  assert.equal(isUrl("https://a b"), false);
  assert.equal(isUrl("ftp://a"), false);
});

test("expandBlankLines keeps every blank line after the first as an empty paragraph", () => {
  assert.equal(expandBlankLines("a\nb"), "a\nb");
  assert.equal(expandBlankLines("a\n\nb"), "a\n\nb", "one blank line is the paragraph break");
  assert.equal(expandBlankLines("a\n\n\nb"), "a\n\n&nbsp;\n\nb", "two blank lines show one empty line");
  assert.equal(expandBlankLines("a\n\n\n\nb"), "a\n\n&nbsp;\n\n&nbsp;\n\nb");
  assert.equal(expandBlankLines("a\n  \n\t\nb"), "a\n\n&nbsp;\n\nb", "whitespace-only lines are blank");
});
