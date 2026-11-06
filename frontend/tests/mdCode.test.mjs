// Where Markdown source is code (shared/lib/mdCode.js): the one reading of
// fences and code spans that a note's paste, the editor's protected spans
// and inline-code marks, and the chat's math share. The note and chat math
// rules themselves stay a declared pair (docs/dev/latex_editing.md).
import assert from "node:assert/strict";
import { test } from "node:test";
import { codeFences, codeSpans, mapProse } from "../src/shared/lib/mdCode.js";
import { protectedSpans } from "../src/editor/mdScan.js";
import { scanMarks, toggleMark } from "../src/editor/mdMarks.js";
import { dollarMath } from "../src/editor/pasteMath.js";
import { mapOutsideCodeFences, normalizeChatMarkdown } from "../src/shared/lib/diagramMarkdown.js";

const spansOf = (text) => codeSpans(text).map(([a, b]) => text.slice(a, b));

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
