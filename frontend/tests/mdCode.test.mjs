// Where Markdown source is code (shared/lib/mdCode.js): the one reading of
// fences and code spans that a note's keys and paste, the editor's protected
// spans and inline-code marks, and the chat's math share, held to what the
// renderer's own parser reads. The cases in tests/shared/codefences.json are
// the backend's too (backend/tests/test_paste_math.py). The note and chat
// math rules themselves stay a declared pair (docs/dev/latex_editing.md).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkParse from "remark-parse";
import { unified } from "unified";
import { codeFences, codeSpans, codeTest, mapProse } from "../src/shared/lib/mdCode.js";
import { findMathAtCursor } from "../src/editor/latexCompletion.js";
import { protectedSpans, scanMathSpans, scanTables } from "../src/editor/mdScan.js";
import { scanMarks, toggleMark } from "../src/editor/mdMarks.js";
import { dollarMath } from "../src/editor/pasteMath.js";
import { mapOutsideCodeFences, normalizeChatMarkdown } from "../src/shared/lib/diagramMarkdown.js";

const FENCES = JSON.parse(readFileSync(new URL("../../tests/shared/codefences.json", import.meta.url), "utf8"));

const spansOf = (text) => codeSpans(text).map(([a, b]) => text.slice(a, b));

// A line with more on it than a quote's marks: a bare ">" is a blank code line.
const filled = (line) => line.replace(/[\s>]/g, "");

// The lines of `text` that are a fence's code, by codeFences.
function codeLines(text) {
  const fences = codeFences(text), out = [];
  let off = 0;
  text.split("\n").forEach((line, i) => {
    const end = off + line.length;
    if (filled(line) && fences.some((f) => off > f.inner && end <= (f.closed ? f.to - 1 : f.to))) out.push(i);
    off = end + 1;
  });
  return out;
}

// The same lines by the parser behind the rendered view.
const parser = unified().use(remarkParse).use(remarkGfm).use(remarkMath);
function renderedLines(text) {
  const lines = text.split(/\r?\n/), out = new Set();
  const walk = (node) => {
    if (node.type === "code") {
      const { line, column } = node.position.start;
      if (/^(`{3,}|~{3,})/.test(lines[line - 1].slice(column - 1))) {
        const count = node.value ? node.value.split(/\r?\n/).length : 0;
        for (let k = 0; k < count; k++) if (filled(lines[line + k])) out.add(line + k);
      }
    }
    (node.children || []).forEach(walk);
  };
  walk(parser.parse(text));
  return [...out].sort((a, b) => a - b);
}

test("the shared cases: the fences both readers find, and the code the renderer shows", () => {
  for (const c of FENCES) {
    assert.deepEqual(codeFences(c.in).map((f) => [f.from, f.inner, f.mark, f.end]), c.fences, c.why);
    assert.deepEqual(codeLines(c.in), c.code, c.why);
    // A formula the note's preprocessing closes (BlockTree's mdPreprocess), which
    // the bare parser leaves open.
    if (!c.preprocessed) assert.deepEqual(renderedLines(c.in), c.code, c.why);
  }
});

test("on random notes the renderer shows the code codeFences reads", () => {
  // Lists that start past 1 or empty stay out, where a paragraph would take them
  // as text, and so do $$ blocks: codeFences reads neither.
  const vocabulary = ["```", "```py", "~~~", "~~~~", "````", "> ```", ">```", "> > ```", "- ```", "* ```", "+ ~~~", "1. ```", "1) ~~~",
    "- - ```", "- > ```", "> - ```", "> 1. ```", "  - ```", "    - ```", "  ```", "   ```", "    ```", "\t```", "- \t```", "-\t```", "   ```  ",
    "code", "> code", "> > code", ">code", "  code", "   code", "    code", "     code", "\tcode", "- item", "* item", "1. item", "", ">", "> ",
    "after", "# head", "---", "- - -", "***", "| a |", "|---|", "- [ ] ```", "- [ ] item", "text `code`",
    "```a``` b", "``` a`b", "~~~ a`b"];
  let seed = 12345;
  const next = (n) => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed % n;
  };
  for (let i = 0; i < 4000; i++) {
    const text = Array.from({ length: 2 + next(6) }, () => vocabulary[next(vocabulary.length)]).join("\n");
    assert.deepEqual(codeLines(text), renderedLines(text), JSON.stringify(text));
  }
});

test("a line of nothing but markers costs its length, not its square", () => {
  const started = performance.now();
  for (const text of ["```\n" + "- ".repeat(100000), "- ".repeat(100000) + "```", "- ".repeat(5000) + "```\n" + "\n".repeat(50000)]) codeFences(text);
  assert.ok(performance.now() - started < 5000);
});

test("a fence's new lines start with its own marks", () => {
  const cont = (text) => codeFences(text)[0].cont;
  assert.equal(cont("```\nx"), "");
  assert.equal(cont("  ```\nx"), "  ", "its indent");
  assert.equal(cont("> ```\nx"), "> ", "a quote's marks");
  assert.equal(cont("> > ~~~\nx"), "> > ");
  assert.equal(cont("- ```\nx"), "  ", "a list item's indent");
  assert.equal(cont("10. ```\nx"), "    ", "however wide its marker");
  assert.equal(cont("> - ```\nx"), ">   ");
  assert.equal(cont("- item\n  ```\nx"), "  ", "under an item");
  assert.equal(cont("- a\n\t- ```\nx"), "\t  ", "a tab stays one");
  assert.equal(cont("[^note]: ```\nx"), "    ", "a footnote's 4, however long its label");
});

test("codeTest: a fence with its delimiters and a code span are code, asked in order", () => {
  const text = "a `b` c\n```\nd\n```\ne";
  const inCode = codeTest(text);
  assert.deepEqual([0, 2, 4, 5, 8, 11, 12, 16, 17, 18].map(inCode), [false, true, true, false, true, true, true, true, false, false]);
  assert.equal(codeTest("no code here")(3), false);
  assert.equal(codeTest("")(0), false);
});

test("a dollar in code is none, and whichever of a formula and a code span starts first holds the other", () => {
  const spans = (text) => scanMathSpans(text).map((s) => text.slice(s.from, s.to));
  assert.deepEqual(spans("`$5` and `$6` tail"), []);
  assert.deepEqual(spans("use `$$` and `$$` here"), []);
  assert.deepEqual(spans("`$` then $a$"), ["$a$"]);
  assert.deepEqual(spans("- ```\n  $a$\n  ```\n$b$"), ["$b$"], "in a listed fence");
  assert.deepEqual(spans("~~~\n$a$\n~~~\n$b$"), ["$b$"], "in a tilde fence");
  assert.deepEqual(spans("$`x`$"), ["$`x`$"], "a formula holds what looks like code");
  assert.deepEqual(spans("$a `b$ c`"), ["$a `b$"]);
  assert.deepEqual(spans("$a`b$ and $c`d$"), ["$a`b$", "$c`d$"], "a backtick in one formula pairs with none in the next");
  assert.deepEqual(spans("$\\text{``a''}$ and $\\text{``bc''}$"), ["$\\text{``a''}$", "$\\text{``bc''}$"]);
  assert.deepEqual(spans("costs $5\n\n`echo $HOME`"), [], "a lone dollar is closed by no code");
  assert.deepEqual(spans("It costs $5.\n\nRun `echo $HOME` first, then $x^2$ is it."), ["$x^2$"], "and ends with its paragraph");
  assert.deepEqual(spans("$$\n~~~ &= c\n$$\n\nthen $x$"), ["$$\n~~~ &= c\n$$", "$x$"], "a formula holds no fence");
  assert.deepEqual(spans("$$~~~x = y$$"), ["$$~~~x = y$$"]);
  const at = (withCaret) => findMathAtCursor(withCaret.replace("|", ""), withCaret.indexOf("|"));
  assert.equal(at("use `$$` for disp|lay"), null);
  assert.equal(at("use `$$`\nsecond li|ne"), null, "on any line after it");
  assert.equal(at("`$5` an|d `$6` tail"), null);
  assert.equal(at("`$x|`"), null, "nor in the code itself");
  assert.equal(at("- ```\n  $$\n  ```\naf|ter"), null);
  assert.deepEqual(at("`a` $x|y$"), { start: 5, end: 7, display: false });
  assert.deepEqual(at("`a` $x|y"), { start: 5, end: 7, display: false }, "still being typed");
  assert.deepEqual(at("$$\nx|\n$$"), { start: 2, end: 5, display: true });
  assert.equal(at("Costs $5 a month.\n\nSet it up: |\n\n```sh\necho $HOME\n```"), null, "a dollar in a fence closes no dollar above");
  assert.equal(at("Costs $5 a month.\n\nSet it up: |\n\nRun `echo $HOME` first."), null);
  assert.deepEqual(at("$$\n`$$`\n|\n$$"), { start: 2, end: 9, display: true }, "nor does a dollar in code close a $$ block");
  assert.deepEqual(at("Costs $5.\n\nRun `echo $HOME`.\n\n$$\nx|\ny\n$$"), { start: 32, end: 37, display: true }, "a lone dollar ends with its paragraph");
  assert.equal(at("Costs $5.\n\nRun `echo $HOME`.\n\n$$\nx\n$$\n\nPlain| text"), null);
  assert.deepEqual(at("Costs $5|.\n\nnext $6"), { start: 7, end: 9, display: false }, "on its own line it is still being typed");
  assert.deepEqual(at("see $a +\nb|$ here"), { start: 5, end: 10, display: false }, "a line break alone ends nothing");
  assert.equal(at("$$\n~~~ &= c\n$$\n\nafter| it"), null, "a formula holds no fence, so its closer closes it");
  assert.deepEqual(at("~~~\n$$\n~~~\n$$\nx|\n$$"), { start: 13, end: 16, display: true }, "a fence holds no formula");
});

test("code spans follow CommonMark's backtick runs", () => {
  assert.deepEqual(spansOf("`a` and `b`"), ["`a`", "`b`"]);
  assert.deepEqual(spansOf("``a`b`` c"), ["``a`b``"], "a run closes only at a run as long");
  assert.deepEqual(spansOf("`a``b`"), ["`a``b`"]);
  assert.deepEqual(spansOf("`` and `code`"), ["`code`"], "an unmatched run is literal");
  assert.deepEqual(spansOf("\\`x`"), [], "an escaped backtick opens nothing");
  assert.deepEqual(spansOf("\\``x`"), ["`x`"], "the rest of an escaped run still opens");
  assert.deepEqual(spansOf("\\\\`x`"), ["`x`"], "an escaped backslash escapes no backtick");
  assert.deepEqual(spansOf("`a\\` b"), ["`a\\`"], "inside code a backslash escapes no closer");
  assert.deepEqual(spansOf("`a\nb`"), [], "a span keeps to its line");
});

test("fences of every shape, and mapProse outside them and the code spans", () => {
  assert.deepEqual(codeFences("> ```js\n> x\n> ```\n~~~\nopen").map((f) => [f.prefix, f.ticks, f.info, f.closed]),
    [["> ", "```", "js", true], ["", "~~~", "", false]]);
  assert.deepEqual(codeFences("```js `x`\ny"), [], "a backtick in a backtick fence's info string opens none");
  assert.deepEqual(codeFences("- ```js\n  x\nafter").map((f) => [f.prefix, f.ticks, f.info, f.closed, f.end]),
    [["- ", "```", "js", false, 11]], "a fence goes with its list item");
  assert.equal(mapProse("- ```\n  a\nb", (s) => s.toUpperCase()), "- ```\n  a\nB");
  const seen = [];
  assert.equal(mapProse("a `b` c\n```\nd\n```\ne", (s) => (seen.push(s), s.toUpperCase())), "A `b` C\n```\nd\n```\nE");
  assert.deepEqual(seen, ["a ", " c\n", "\ne"]);
});

test("mapOutsideCodeFences keeps each fence with its closing line break", () => {
  const seen = [];
  const text = "a\n```\nb\n```\nc";
  assert.equal(mapOutsideCodeFences(text, (s) => (seen.push(s), s)), text);
  assert.deepEqual(seen, ["a\n", "c"]);
  assert.equal(mapOutsideCodeFences("", (s) => s + "!"), "!");
});

test("the editor protects every fence the renderer shows, and finds no table in one", () => {
  for (const fence of ["- ```\n  [[x]]\n  ```", "~~~\n[[x]]\n~~~", "> ```\n> [[x]]\n> ```"]) {
    const spans = protectedSpans(`${fence}\n[[y]]`).map((s) => `${fence}\n[[y]]`.slice(s.from, s.to));
    assert.deepEqual(spans, [fence]);
  }
  assert.deepEqual(scanTables("~~~\n| a |\n|---|\n~~~"), []);
});

test("the editor protects the code spans a paste reads as code", () => {
  const text = "``a`[[x]]`` and [[y]]";
  const spans = protectedSpans(text).map((s) => text.slice(s.from, s.to));
  assert.deepEqual(spans, ["``a`[[x]]``"]);
  assert.equal(dollarMath("``a`\\(x\\)`` and \\(y\\)"), "``a`\\(x\\)`` and $y$");
});

test("a double-backtick span is one inline-code mark, unwrapped whole", () => {
  const [code] = scanMarks("x ``a`**b**`` y");
  assert.deepEqual([code.marker, code.from, code.to, code.openLen, code.closeLen], ["`", 2, 13, 2, 2]);
  assert.deepEqual(scanMarks("``a`**b**``").map((s) => s.marker), ["`"], "nothing is bold inside it");
  const r = toggleMark("x ``a`b`` y", 5, 5, "`");
  let out = "x ``a`b`` y";
  for (const c of [...r.changes].sort((p, q) => q.from - p.from)) out = out.slice(0, c.from) + c.insert + out.slice(c.to);
  assert.equal(out, "x a`b y");
  assert.deepEqual(r.selection, { anchor: 3, head: 3 });
  // A caret inside either delimiter's run unwraps the span whole too.
  for (const at of [3, 8]) {
    const t = toggleMark("x ``a`b`` y", at, at, "`");
    let s = "x ``a`b`` y";
    for (const c of [...t.changes].sort((p, q) => q.from - p.from)) s = s.slice(0, c.from) + c.insert + s.slice(c.to);
    assert.equal(s, "x a`b y", `caret at ${at}`);
  }
});

test("code spans scan a line of many backtick runs in linear time", () => {
  const line = "`a ".repeat(40000);
  const t0 = performance.now();
  assert.equal(codeSpans(line).length, 20000);
  assert.ok(performance.now() - t0 < 500, "40,000 runs on one line");
});

test("chat replies and notes are a declared pair: one code reading, two math rules", () => {
  // In a reply every \[ \] is display math; in a note an unpadded \[x\] is an escaped bracket.
  assert.equal(normalizeChatMarkdown("\\[x\\]"), "\n$$\nx\n$$\n");
  assert.equal(dollarMath("\\[x\\]"), "\\[x\\]");
  assert.equal(normalizeChatMarkdown("see \\(a\\)"), "see $a$");
  // Both leave code as written: fences, one-backtick and two-backtick spans.
  for (const code of ["`\\(x\\)`", "``a`\\(x\\)``", "```\n\\[x\\]\n```"]) {
    assert.equal(normalizeChatMarkdown(`${code}\n\\(y\\)`), `${code}\n$y$`, code);
    assert.equal(dollarMath(`${code}\n\\(y\\)`), `${code}\n$y$`, code);
  }
});
