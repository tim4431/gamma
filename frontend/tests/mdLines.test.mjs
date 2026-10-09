// Quote marks and list markers on a note's lines (editor/mdLines.js): what
// the line-break Enter continues, what a paste onto a quoted line gives every
// line, and Toggle quote.
import assert from "node:assert/strict";
import { test } from "node:test";
import { enterPlan, quoteMarksAt, quotePasted, toggleQuotePlan } from "../src/editor/mdLines.js";

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

test("a paste onto a quoted line quotes every pasted line", () => {
  assert.equal(quotePasted("> ", 2, "one\ntwo\n\nthree"), "one\n> two\n>\n> three");
  assert.equal(quotePasted("> > x", 5, "a\nb"), "a\n> > b");
  assert.equal(quotePasted("> > x", 5, "a\n\nb"), "a\n> >\n> > b");
  assert.equal(quotePasted("plain", 5, "a\nb"), "a\nb", "not on a quote line");
  assert.equal(quotePasted("> x", 0, "a\nb"), "a\nb", "the caret before the marks");
  assert.equal(quotePasted("> x", 3, "one line"), "one line");
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
