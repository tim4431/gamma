// The structure of a note's lines (editor/mdLines.js): what the line-break
// Enter continues, where a paste's lines go, what the "/" menu's line
// commands make of a line, and Toggle quote.
import assert from "node:assert/strict";
import { test } from "node:test";
import { enterPlan, parseLine, pastePlacements, quoteMarksAt, retypeLine, toggleQuotePlan } from "../src/editor/mdLines.js";
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
const paste = (withSel, pasted) => {
  const caret = withSel.indexOf("|");
  const from = caret >= 0 ? caret : withSel.indexOf("«");
  const text = withSel.replace(/[|«»]/g, "");
  const to = caret >= 0 ? caret : withSel.indexOf("»") - 1;
  const r = pastePlacements(text, from, to, pasted);
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
});

test("the / menu runs them on the line of the typed command", () => {
  let out;
  const ctx = (value, query) => ({ value, start: value.indexOf(query), cursor: value.indexOf(query) + query.length, setText: (v, a) => { out = [v, a]; } });
  TEXT_INSERTS.todo(ctx("> a\n> - item/todo\n> b", "/todo"));
  assert.deepEqual(out, ["> a\n> - [ ] item\n> b", 16]);
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
