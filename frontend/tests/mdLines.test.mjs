// The structure of a note's lines (editor/mdLines.js): what the line-break
// Enter continues, what the new-block key and Backspace do to a block, where
// a paste's lines go, what the "/" menu's line commands make of a line, and
// Toggle quote.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  backspacePlan, enterPlan, isWholeLine, newBlockPlan, parseLine, pastePlacements, quoteMarksAt, retypeLine, toggleQuotePlan, withoutFirstMarker,
} from "../src/editor/mdLines.js";
import { codeBreak, splitPlan } from "../src/editor/blockCommands.js";
import { fenceInnerAt } from "../src/editor/fences.js";
import { TEXT_INSERTS } from "../src/editor/slashInserts.js";

// The text after a plan, with "|" for the caret (a selection "«…»").
const apply = (text, changes) => {
  let out = text;
  for (const c of [...changes].sort((a, b) => b.from - a.from)) out = out.slice(0, c.from) + c.insert + out.slice(c.to);
  return out;
};
const enter = (withCaret) => {
  const pos = withCaret.indexOf("|");
  const text = withCaret.replace("|", "");
  const p = enterPlan(text, pos);
  if (!p) return null;
  const out = apply(text, [p]);
  return out.slice(0, p.pos) + "|" + out.slice(p.pos);
};
const toggle = (withSel) => {
  const caret = withSel.indexOf("|");
  const from = caret >= 0 ? caret : withSel.indexOf("«");
  const text = withSel.replace(/[|«»]/g, "");
  const to = caret >= 0 ? caret : withSel.indexOf("»") - 1;
  const { changes, selection } = toggleQuotePlan(text, from, to);
  const out = apply(text, changes);
  const { anchor, head } = selection;
  return anchor === head
    ? out.slice(0, anchor) + "|" + out.slice(anchor)
    : out.slice(0, anchor) + "«" + out.slice(anchor, head) + "»" + out.slice(head);
};

// A press of the new-block key on "|" (or over a selection «…»), as text:
// "this ⏎ new", the caret's "|" where it lands; "new ↥ this" for a block made
// above, the caret staying; "this ▪" when no block is made.
const newBlock = (withCaret, plan = newBlockPlan) => {
  const selected = !withCaret.includes("|");
  const from = selected ? withCaret.indexOf("«") : withCaret.indexOf("|");
  const to = selected ? withCaret.indexOf("»") - 1 : from;
  const p = plan(withCaret.replace(/[|«»]/g, ""), from, to);
  const at = (s, n) => s.slice(0, n) + "|" + s.slice(n);
  if (p.stay) return `${at(p.keep, p.caret)} ▪`;
  if (p.above) return `${p.next.trimEnd()} ↥ ${at(p.keep, from)}`;
  return `${p.keep} ⏎ ${at(p.next, p.caret)}`;
};

test("the new-block key splits a paragraph at the caret", () => {
  assert.equal(newBlock("plain|"), "plain ⏎ |");
  assert.equal(newBlock("ab|cd"), "ab ⏎ |cd");
  assert.equal(newBlock("|"), " ⏎ |", "an empty note makes an empty one");
});

test("the new-block key before a block's text makes the new block above and stays", () => {
  assert.equal(newBlock("|abcd"), " ↥ |abcd");
  assert.equal(newBlock("- |ab"), "- ↥ - |ab");
  assert.equal(newBlock("- [x] |ab"), "- [ ] ↥ - [x] |ab", "the box above is an open one");
  assert.equal(newBlock("2. |ab"), "2. ↥ 2. |ab", "a number is typed text and does not count back");
  assert.equal(newBlock("> - |ab"), "> - ↥ > - |ab");
  assert.equal(newBlock("|- ab"), " ↥ |- ab", "before the marker, the block above is plain");
  assert.equal(newBlock("> |ab"), " ↥ > |ab", "a quote is no list: what comes above it is plain");
  assert.equal(newBlock("# |ab"), " ↥ # |ab");
  assert.equal(newBlock("|```\ncode\n```"), " ↥ |```\ncode\n```", "code can get a block above it");
  assert.equal(newBlock("|---"), " ↥ |---");
});

test("the new-block key continues a list", () => {
  assert.equal(newBlock("- ab|"), "- ab ⏎ - |");
  assert.equal(newBlock("- ab|cd"), "- ab ⏎ - |cd");
  assert.equal(newBlock("3. ab|"), "3. ab ⏎ 4. |");
  assert.equal(newBlock("3) ab|cd"), "3) ab ⏎ 4) |cd");
  assert.equal(newBlock("- [x] ab|"), "- [x] ab ⏎ - [ ] |");
  assert.equal(newBlock("- [ ] a|b"), "- [ ] a ⏎ - [ ] |b");
  assert.equal(newBlock("> - ab|"), "> - ab ⏎ > - |", "a list in a quote stays in it");
  assert.equal(newBlock("- a\n  - b|"), "- a\n  - b ⏎ - |", "a block has no indentation of its own");
});

test("the new-block key on an empty item that is the whole block ends the innermost thing", () => {
  assert.equal(newBlock("- |"), "| ▪");
  assert.equal(newBlock("- [ ] |"), "| ▪");
  assert.equal(newBlock("- [x] |"), "| ▪");
  assert.equal(newBlock("1. |"), "| ▪");
  assert.equal(newBlock("> - |"), "> | ▪", "the list goes, the quote stays");
  assert.equal(newBlock("> > |"), "> | ▪", "one quote level goes");
  assert.equal(newBlock("> |"), "| ▪");
  assert.equal(newBlock("|- "), " ↥ |- ", "before the marker nothing ends");
  assert.equal(newBlock("> [!note] |"), "> [!note]  ⏎ |", "a callout with no title yet is no empty item");
});

test("the new-block key on an empty item with lines around it drops the line and splits there", () => {
  assert.equal(newBlock("- a\n- |"), "- a ⏎ |");
  assert.equal(newBlock("- a\n- [ ] |"), "- a ⏎ |");
  assert.equal(newBlock("1. a\n2. |"), "1. a ⏎ |");
  assert.equal(newBlock("> - a\n> - |"), "> - a ⏎ |");
  assert.equal(newBlock("> a\n> |"), "> a ⏎ |");
  assert.equal(newBlock("- a\n- |\n- b"), "- a ⏎ - |b");
  assert.equal(newBlock("- |\n- b"), " ⏎ - |b");
});

test("the new-block key: a heading's text goes on plain, a quote's in its marks", () => {
  assert.equal(newBlock("# a|b"), "# a ⏎ |b");
  assert.equal(newBlock("# ab|"), "# ab ⏎ |");
  assert.equal(newBlock("> ab|"), "> ab ⏎ |");
  assert.equal(newBlock("> [!tip] t|"), "> [!tip] t ⏎ |");
  assert.equal(newBlock("> a|b"), "> a ⏎ > |b");
  assert.equal(newBlock("> > a|b"), "> > a ⏎ > > |b");
  assert.equal(newBlock("> [!tip] a|b"), "> [!tip] a ⏎ > |b");
});

test("the new-block key in a block of several lines takes the lines after the caret along", () => {
  assert.equal(newBlock("a\nb|\nc"), "a\nb ⏎ |c");
  assert.equal(newBlock("a|b\nc"), "a ⏎ |b\nc");
  assert.equal(newBlock("- a|\n- b"), "- a ⏎ - |b", "its own marker");
  assert.equal(newBlock("- a|b\n- c"), "- a ⏎ - |b\n- c");
  assert.equal(newBlock("> a|\n> b"), "> a ⏎ > |b");
  assert.equal(newBlock("a\n|b"), "a ⏎ |b", "at a line's start");
  assert.equal(newBlock("a\n- |b"), "a ⏎ - |b", "at an item's");
  assert.equal(newBlock("a\n|"), "a ⏎ |", "an empty last line goes");
  assert.equal(newBlock("a|\n\nb"), "a ⏎ |b", "the blank lines at the cut go");
  assert.equal(newBlock("a\n|\nb"), "a ⏎ |b");
});

test("the new-block key leaves a table line, a divider or a delimiter whole", () => {
  // (A table has bars of its own: these give the caret as a number.)
  assert.deepEqual(newBlockPlan("| a | b |", 4), { keep: "| a | b |", next: "", caret: 0 });
  assert.deepEqual(newBlockPlan("| a | b |", 9), { keep: "| a | b |", next: "", caret: 0 });
  assert.deepEqual(newBlockPlan("x\n| a |\n| c |", 5), { keep: "x\n| a |\n| c |", next: "", caret: 0 });
  assert.deepEqual(newBlockPlan("> | a |", 5), { keep: "> | a |", next: "", caret: 0 });
  assert.equal(newBlock("---|"), "--- ⏎ |");
  assert.equal(newBlock("--|-"), "--- ⏎ |");
  assert.equal(newBlock("***|"), "*** ⏎ |");
  assert.equal(newBlock("> ---|"), "> --- ⏎ |");
  assert.equal(newBlock("``|`\ncode\n```"), "```\ncode\n``` ⏎ |");
});

test("a selection goes first, then the rules apply at its start", () => {
  assert.equal(newBlock("a«bc»d"), "a ⏎ |d");
  assert.equal(newBlock("«ab»cd"), " ↥ |cd");
  assert.equal(newBlock("«abcd»"), " ⏎ |");
  assert.equal(newBlock("- «ab»"), "| ▪", "what is left is an empty item");
  assert.equal(newBlock("- a«b\n- c»d"), "- a ⏎ - |d");
});

test("the new-block key never cuts a formula: the split moves past its closing $", () => {
  const split = (withCaret) => newBlock(withCaret, splitPlan);
  assert.equal(split("$x^2|$"), "$x^2$ ⏎ |");
  assert.equal(split("$|x$"), "$x$ ⏎ |");
  assert.equal(split("see $a|b$ here"), "see $ab$ ⏎ |here", "the space that led the rest goes");
  assert.equal(split("$a|$  \t b"), "$a$ ⏎ |b");
  assert.equal(split("$a|$   "), "$a$ ⏎ |", "only spaces follow");
  assert.equal(split("$a|$ $b$"), "$a$ ⏎ |$b$", "the next formula goes on whole");
  assert.equal(split("$a$ and $b|$ c"), "$a$ and $b$ ⏎ |c", "the formula the caret is in");
  assert.equal(split("$x|$\nmore"), "$x$ ⏎ |more");
  assert.equal(split("one\n$x|$ b\nmore"), "one\n$x$ ⏎ |b\nmore");
});

test("a stray $ on another line takes no formula apart", () => {
  const split = (withCaret) => newBlock(withCaret, splitPlan);
  assert.equal(split("- costs $5\n- $x|$ more"), "- costs $5\n- $x$ ⏎ - |more");
  assert.equal(split("costs $5\nsee $x^2|$"), "costs $5\nsee $x^2$ ⏎ |");
  assert.equal(split("$x\n$y|$"), "$x\n$y$ ⏎ |");
  assert.equal(split("costs $5\n\nsee $a|b$ c"), "costs $5\n\nsee $ab$ ⏎ |c");
  assert.equal(split("$x|$\ncosts $5"), "$x$ ⏎ |costs $5", "and one below");
  assert.equal(split("costs $5\nsee |this"), "costs $5\nsee  ⏎ |this", "no formula on the caret's line");
});

test("the states an auto-paired closer waits in are a formula too", () => {
  const split = (withCaret) => newBlock(withCaret, splitPlan);
  assert.equal(split("see $|$ here"), "see $$ ⏎ |here", "an empty pair");
  assert.equal(split("$ |$"), "$ $ ⏎ |", "only a space in it");
  assert.equal(split("see $«x»$ here"), "see $$ ⏎ |here", "all of it selected");
  assert.equal(split("$\\|$"), "$\\$ ⏎ |", "a \\command being started");
  assert.equal(split("$a \\|$"), "$a \\$ ⏎ |");
  assert.equal(split("see $a\\|$ here"), "see $a\\$ ⏎ |here");
  assert.equal(split("$a\\|$b$"), "$a\\$b$ ⏎ |", "an escaped $ inside a closed formula");
  assert.equal(split("$a$|$b$"), "$a$ ⏎ |$b$", "two touching formulas are no pair");
  assert.equal(split("cost \\$5\\|$6"), "cost \\$5\\ ⏎ |$6", "a $ alone is no closer");
});

test("a list or a quote goes on past a formula", () => {
  const split = (withCaret) => newBlock(withCaret, splitPlan);
  assert.equal(split("- $x|$"), "- $x$ ⏎ - |");
  assert.equal(split("- $a|b$ c"), "- $ab$ ⏎ - |c");
  assert.equal(split("3. $x|$"), "3. $x$ ⏎ 4. |");
  assert.equal(split("- [x] $x|$ c"), "- [x] $x$ ⏎ - [ ] |c");
  assert.equal(split("> $a|b$ c"), "> $ab$ ⏎ > |c");
  assert.equal(split("# $x|$"), "# $x$ ⏎ |");
});

test("a selection starting in a formula goes, then the split moves past what is left of it", () => {
  const split = (withCaret) => newBlock(withCaret, splitPlan);
  assert.equal(split("$a«b»c$ d"), "$ac$ ⏎ |d");
  assert.equal(split("$a«bc»$"), "$a$ ⏎ |");
  assert.equal(split("$a«b$ c»d"), "$a ⏎ |d", "the closer went with it");
  assert.equal(split("a «b $c»d$"), "a  ⏎ |d$", "the selection started outside");
});

test("the new-block key splits at the caret where there is no formula to keep", () => {
  const split = (withCaret) => newBlock(withCaret, splitPlan);
  assert.equal(split("$x|"), "$x ⏎ |", "no closer");
  assert.equal(split("$a|b"), "$a ⏎ |b");
  assert.equal(split("- $x|"), "- $x ⏎ - |");
  assert.equal(split("$a|\nb$"), "$a ⏎ |b$", "the closer on another line: inline math is one line");
  assert.equal(split("cost \\$5| or \\$6"), "cost \\$5 ⏎ | or \\$6", "escaped dollars");
  assert.equal(split("|$x$ b"), " ↥ |$x$ b", "before the formula");
  assert.equal(split("$x$| b"), "$x$ ⏎ | b", "after it, the rest as anywhere else");
  assert.equal(split("ab|cd"), "ab ⏎ |cd");
  assert.equal(split("- ab|cd"), "- ab ⏎ - |cd");
  // (A table has bars of its own: the caret as a number.)
  assert.deepEqual(splitPlan("| $a$ | b |", 4), { keep: "| $a$ | b |", next: "", caret: 0 }, "a table row stays uncut, its spaces too");
});

test("Backspace right after a list marker takes the marker, on any line", () => {
  const press = (withCaret) => {
    const pos = withCaret.indexOf("|");
    const text = withCaret.replace("|", "");
    const p = backspacePlan(text, pos);
    if (!p) return null;
    const out = text.slice(0, p.from) + text.slice(p.to);
    return out.slice(0, p.pos) + "|" + out.slice(p.pos);
  };
  assert.equal(press("- |x"), "|x");
  assert.equal(press("* |x"), "|x");
  assert.equal(press("1. |x"), "|x");
  assert.equal(press("12) |x"), "|x");
  assert.equal(press("- [ ] |x"), "|x");
  assert.equal(press("- [x] |x"), "|x");
  assert.equal(press("- |"), "|");
  assert.equal(press("> - |x"), "> |x", "the quote stays");
  assert.equal(press("a\n- |x\nb"), "a\n|x\nb", "on any line");
  assert.equal(press("  - |x"), "  |x");
  assert.equal(press("- x|"), null);
  assert.equal(press("-| x"), null);
  assert.equal(press("|- x"), null);
  assert.equal(press("# |x"), null, "a heading stays raw Markdown");
  assert.equal(press("> |x"), null, "a quote too");
  assert.equal(press("> [!tip] |x"), null, "and a callout");
});

test("the lines a merge will not join, and the first line it takes", () => {
  for (const line of ["| a |", "> | a |", "---", "***", "___", "- - -", "```", "```py", "~~~", "~~~py", "> ~~~", "$$", "$$x$$"]) assert.ok(isWholeLine(line), line);
  for (const line of ["plain", "- a", "# a", "> a", "--", "a | b", "$x$"]) assert.ok(!isWholeLine(line), line);
  assert.equal(withoutFirstMarker("- a\n- b"), "a\n- b");
  assert.equal(withoutFirstMarker("- [x] a"), "a");
  assert.equal(withoutFirstMarker("3. a"), "a");
  assert.equal(withoutFirstMarker("## a\nb"), "a\nb");
  assert.equal(withoutFirstMarker("plain"), "plain");
});

// Where Enter is a line break: "|" marks the caret.
const inCode = (withCaret) => {
  const pos = withCaret.indexOf("|");
  return !!fenceInnerAt(withCaret.slice(0, pos) + withCaret.slice(pos + 1), pos);
};

test("code is a fence of backticks or tildes, also in a quote or a callout", () => {
  for (const text of [
    "```py\nab|cd\n```", "~~~py\nab|cd\n~~~", "````\nab|cd\n````",
    "> ```py\n> ab|cd\n> ```", ">```py\n>ab|cd\n>```", "> > ~~~\n> > ab|cd\n> > ~~~",
    "> [!note] t\n> ```py\n> ab|cd\n> ```",
    "```py|\nab\n```", "```\nab\n|```", "> ```py|\n> ab\n> ```", "> ```\n> ab\n> |```",
    "```\nstill open|", "> ~~~\n> still open|", "~~~\n|",
  ]) assert.ok(inCode(text), text);
  for (const text of [
    "|```\nab\n```", "```\nab\n```|", "``|`\nab\n```", "> ```\n> ab\n> ```|", "~~~\nab\n~~~\nafter|",
    "plain|", "```a``` inline|", "~~ strike ~~|",
  ]) assert.ok(!inCode(text), text);
});

test("a fence closes on its own mark, at least as long, at its own depth", () => {
  assert.ok(inCode("```\n~~~\nab|\n```"), "tildes do not close backticks");
  assert.ok(inCode("~~~\n```\nab|\n~~~"), "nor the other way round");
  assert.ok(inCode("````\n```\nab|\n````"), "nor a shorter fence");
  assert.ok(inCode("```\n``` py\nab|\n```"), "nor one with words after it");
  assert.ok(!inCode("~~~~\nab\n~~~~~\nafter|"), "a longer one does");
  assert.ok(inCode("```\n> ```\nab|\n```"), "a quoted fence closes no plain one");
  assert.ok(inCode("> ```\n> > ```\n> ab|\n> ```"), "nor a deeper one a quoted one");
  assert.ok(!inCode("> ```\n> code\nafter|"), "the quote's end ends its fence");
  assert.ok(inCode("> ```\n> code\n```\nnew fence|"), "and the line after it starts afresh");
});

test("a line break in code keeps the fence's own marks", () => {
  const brk = (withCaret) => {
    const pos = withCaret.indexOf("|");
    return codeBreak(withCaret.slice(0, pos) + withCaret.slice(pos + 1), pos);
  };
  assert.equal(brk("```\nab|\n```"), "\n");
  assert.equal(brk("~~~\nab|\n~~~"), "\n");
  assert.equal(brk("> ```\n> ab|\n> ```"), "\n> ");
  assert.equal(brk("> > ~~~\n> > ab|\n> > ~~~"), "\n> > ");
  assert.equal(brk("> [!note] t\n> ```\n> ab|\n> ```"), "\n> ");
  assert.equal(brk("```\n> ab|\n```"), "\n", "a > in a plain fence is code");
  assert.equal(brk("> ```\n> > ab|\n> ```"), "\n> ", "and the inner level in a quoted one");
  assert.equal(brk("> $$\n> x|\n> $$"), "\n> ", "math keeps the line's marks");
  assert.equal(brk("plain|"), null);
  assert.equal(brk("> quoted|"), null);
});

test("the new-block key does not cut a tilde fence's delimiter", () => {
  assert.deepEqual(newBlockPlan("~~~py\ncode\n~~~", 2), { keep: "~~~py\ncode\n~~~", next: "", caret: 0 });
  assert.deepEqual(newBlockPlan("~~~py\ncode\n~~~", 15), { keep: "~~~py\ncode\n~~~", next: "", caret: 0 });
  assert.deepEqual(newBlockPlan("> ~~~\n> code\n> ~~~", 19), { keep: "> ~~~\n> code\n> ~~~", next: "", caret: 0 });
});

test("Enter continues a quote, a list and both together", () => {
  assert.equal(enter("> one|"), "> one\n> |");
  assert.equal(enter("> [!note] Title|"), "> [!note] Title\n> |");
  assert.equal(enter("> > deep|"), "> > deep\n> > |");
  assert.equal(enter("> - item|"), "> - item\n> - |");
  assert.equal(enter(">   - sub|"), ">   - sub\n>   - |");
  assert.equal(enter("> - [x] done|"), "> - [x] done\n> - [ ] |");
  assert.equal(enter("> 3. third|"), "> 3. third\n> 4. |");
  assert.equal(enter("- [ ] task|"), "- [ ] task\n- [ ] |");
  assert.equal(enter("  - nested|"), "  - nested\n  - |");
});

test("Enter before the text of an item opens a new item above it", () => {
  assert.equal(enter("> |rest"), "> \n> |rest");
  assert.equal(enter("- |rest"), "- \n- |rest");
});

test("Enter on an empty item ends the innermost thing", () => {
  assert.equal(enter("> a\n> - |"), "> a\n> |", "the list ends, the quote goes on");
  assert.equal(enter("> a\n> > |"), "> a\n> |", "one quote level goes");
  assert.equal(enter("> a\n> |"), "> a\n|", "the quote ends");
  assert.equal(enter("> a\n>|"), "> a\n|", "a bare > too");
  assert.equal(enter("- a\n- |"), "- a\n|");
});

test("Enter on a plain line, or inside the marks, is a plain newline", () => {
  assert.equal(enter("plain|"), null);
  assert.equal(enter(">| a"), null);
  assert.equal(enter("|> a"), null);
  assert.equal(enter("-not a list|"), null);
});

test("quote marks at the caret", () => {
  assert.equal(quoteMarksAt("> a", 3), "> ");
  assert.equal(quoteMarksAt("x\n> > a", 5), "> > ");
  assert.equal(quoteMarksAt("> a", 0), "");
  assert.equal(quoteMarksAt("a", 1), "");
});

// A paste at "|" (a selection "«…»"): the note after each placement, by name.
const paste = (withSel, pasted, copied) => {
  const caret = withSel.indexOf("|");
  const from = caret >= 0 ? caret : withSel.indexOf("«");
  const text = withSel.replace(/[|«»]/g, "");
  const to = caret >= 0 ? caret : withSel.indexOf("»") - 1;
  const r = pastePlacements(text, from, to, pasted, copied);
  return r && Object.fromEntries(r.options.map((o) => [o.name, apply(text, [{ from: r.from, to: r.to, insert: o.insert }])]));
};

test("a line's parts: quote marks, indentation, marker, text", () => {
  assert.deepEqual(parseLine("> >   - [ ] milk"),
    { quote: "> > ", depth: 2, indent: "  ", marker: "- [ ] ", kind: "task", body: "milk" });
  assert.equal(parseLine("> [!tip]- Title").kind, "callout");
  assert.equal(parseLine("[!tip] not in a quote").kind, null);
  assert.equal(parseLine("## Heading").kind, "heading");
  assert.equal(parseLine("12) twelve").kind, "number");
  assert.equal(parseLine("**bold**").kind, null);
});

test("lines copied out of a quote join the quote they land in", () => {
  assert.deepEqual(paste("> [!tip] Steps\n> |", "> 1. this\n> 2. second"), {
    here: "> [!tip] Steps\n> 1. this\n> 2. second",
    nested: "> [!tip] Steps\n> > 1. this\n> > 2. second",
    copied: "> [!tip] Steps\n> > 1. this\n> 2. second",
  });
  assert.deepEqual(paste("> |", "one\ntwo\n\nthree"), {
    here: "> one\n> two\n>\n> three",
    nested: "> > one\n> > two\n> >\n> > three",
    copied: "> one\ntwo\n\nthree",
  });
});

test("a callout pasted into a quote stays a box inside it", () => {
  assert.deepEqual(paste("> Some quote\n> |", "> [!warning] Careful\n> hot plate"), {
    here: "> Some quote\n> > [!warning] Careful\n> > hot plate",
    copied: "> Some quote\n> > [!warning] Careful\n> hot plate",
  });
  assert.equal(paste("text|", "> [!tip] T\n> body").here, "text\n> [!tip] T\n> body", "after text, on its own line");
});

test("list items land beside the item or under it, keeping their sub-items", () => {
  assert.deepEqual(paste("> [!note] Shop\n> - [ ] milk|", "- [ ] eggs\n- [ ] bread\n  - [ ] rye"), {
    here: "> [!note] Shop\n> - [ ] milk\n> - [ ] eggs\n> - [ ] bread\n>   - [ ] rye",
    nested: "> [!note] Shop\n> - [ ] milk\n>   - [ ] eggs\n>   - [ ] bread\n>     - [ ] rye",
    copied: "> [!note] Shop\n> - [ ] milk- [ ] eggs\n- [ ] bread\n  - [ ] rye",
  });
  const d = paste("- groceries\n  - milk|", "- eggs\n  - free range\n- bread");
  assert.equal(d.here, "- groceries\n  - milk\n  - eggs\n    - free range\n  - bread");
  assert.equal(paste("1. one|", "- a\n- b").nested, "1. one\n   - a\n   - b", "under a number: its text column");
  assert.equal(paste("- top|", "    - a\n      - b").here, "- top\n- a\n  - b", "the indentation they share goes");
});

test("an empty item takes the first pasted line's place", () => {
  assert.deepEqual(paste("> - |", "- eggs\n- bread"), { here: "> - eggs\n> - bread", copied: "> - - eggs\n- bread" });
  assert.equal(paste("> - «old»", "- a\n- b").here, "> - a\n> - b", "a selection goes with it");
  assert.deepEqual(paste("> - |", "- eggs"), { here: "> - eggs", copied: "> - - eggs" }, "one line too");
  assert.equal(paste("> |", "> quoted").here, "> quoted", "a quoted line merges");
  assert.equal(paste("> text|", "- x"), null, "one line after text pastes as it is");
});

test("in a list, pasted lines of text become items", () => {
  assert.equal(paste("> - |", "eggs\nbread").here, "> - eggs\n> - bread");
  assert.deepEqual(paste("3. three|", "four\n\nfive"), {
    here: "3. threefour\n\n4. five",
    nested: "3. threefour\n\n   1. five",
    copied: "3. threefour\n\nfive",
  });
  assert.equal(paste("- [x] done|", "> a\n> b").here, "- [x] done\n> a\n> b", "a quote stays a quote");
});

test("before a line's text a pasted list goes above it", () => {
  assert.deepEqual(paste("- |milk", "- eggs\n- bread"), { here: "- eggs\n- bread\n- milk", copied: "- - eggs\n- breadmilk" });
});

test("plain text on a plain line, and one line inline, paste as they are", () => {
  assert.equal(paste("plain|", "a\nb"), null);
  assert.equal(paste("|", "a\nb"), null);
  assert.equal(paste("> quote|", "more"), null);
});

test("with its math converted, As copied is still the clipboard's own text", () => {
  assert.deepEqual(paste("plain|", "$a$\n$$b^2$$", "\\(a\\)\n\\[b^2\\]"),
    { here: "plain$a$\n$$b^2$$", copied: "plain\\(a\\)\n\\[b^2\\]" });
  assert.deepEqual(paste("- |", "- $x$\n- y", "- \\(x\\)\n- y"),
    { here: "- $x$\n- y", copied: "- - \\(x\\)\n- y" });
});

test("a paste over a selection takes its place", () => {
  assert.deepEqual(paste("Energy: «TBD». End.", "$a$\n$b$", "\\(a\\)\n\\(b\\)"),
    { here: "Energy: $a$\n$b$. End.", copied: "Energy: \\(a\\)\n\\(b\\). End." });
  assert.deepEqual(paste("- one «two» three", "- a\n- b"),
    { here: "- one \n- a\n- b three", nested: "- one \n  - a\n  - b three", copied: "- one - a\n- b three" });
});

test("the / menu's line commands change what a line is and keep where it sits", () => {
  const retype = (line, kind, above = "") => retypeLine(line, line.length, kind, above).line;
  assert.equal(retype("> - item", "todo"), "> - [ ] item");
  assert.equal(retype("> text", "h2"), "> ## text");
  assert.equal(retype("# heading", "h2"), "## heading", "a heading re-levels");
  assert.equal(retype("  - sub", "number"), "  1. sub");
  assert.equal(retype("> 1. one", "bullet"), "> - one");
  assert.equal(retype("> text", "quote"), "> > text");
  assert.equal(retype("# Title", "quote"), "> # Title");
  assert.equal(retype("> text", "callout"), "> [!note] text", "the quote this line starts becomes a callout");
  assert.equal(retype("> > ", "callout", "> outer"), "> > [!note] ", "so does an inner one");
  assert.equal(retype("> body", "callout", "> [!tip] T"), "> > [!note] body", "mid-quote, a new box one level in");
  assert.equal(retype("text", "callout"), "> [!note] text");
  assert.deepEqual(retypeLine("- milk", 4, "todo"), { line: "- [ ] milk", caret: 8 }, "the caret stays in the text");
  assert.equal(retype("- item", "text"), "item", "/text takes the marker away");
  assert.equal(retype("- [x] item", "text"), "item");
  assert.equal(retype("1. item", "text"), "item");
  assert.equal(retype("## item", "text"), "item");
  assert.equal(retype("> - item", "text"), "> item", "the quote marks stay");
  assert.equal(retype("> [!tip] title", "text"), "> title");
  assert.equal(retype("plain", "text"), "plain");
  assert.deepEqual(retypeLine("- milk", 4, "text"), { line: "milk", caret: 2 }, "the caret stays in the text");
});

test("the / menu runs them on the line of the typed command", () => {
  let out;
  const ctx = (value, query) => ({ value, start: value.indexOf(query), cursor: value.indexOf(query) + query.length, setText: (v, a) => { out = [v, a]; } });
  TEXT_INSERTS.todo(ctx("> a\n> - item/todo\n> b", "/todo"));
  assert.deepEqual(out, ["> a\n> - [ ] item\n> b", 16]);
  TEXT_INSERTS.text(ctx("> a\n> - item/text\n> b", "/text"));
  TEXT_INSERTS.callout(ctx("> a\n> > /callout", "/callout"));
  assert.deepEqual(out, ["> a\n> > [!note] ", 16]);
});

test("Toggle quote marks every selected line, blank ones with a bare >", () => {
  assert.equal(toggle("«one\n\ntwo»"), "«> one\n>\n> two»");
  assert.equal(toggle("o|ne"), "> o|ne", "the caret moves with its text");
  assert.equal(toggle("a\n«b\n»c"), "a\n«> b\n»c", "a selection ending at a line start leaves that line");
});

test("Toggle quote on quoted lines takes one level off", () => {
  assert.equal(toggle("«> one\n>\n> two»"), "«one\n\ntwo»");
  assert.equal(toggle("> > o|ne"), "> o|ne");
  assert.equal(toggle("«> one\nplain»"), "«> > one\n> plain»", "a mixed selection is quoted");
  assert.equal(toggle("> o|ne"), "o|ne");
  assert.equal(toggle(">| one"), "|one", "a caret inside the marks lands where they were");
});

test("Toggle quote off a callout drops its marker; a nested one keeps it", () => {
  assert.equal(toggle("«> [!note] Title\n> body»"), "«Title\nbody»");
  assert.equal(toggle("«> > [!tip]- T\n> > b»"), "«> [!tip]- T\n> b»");
});
