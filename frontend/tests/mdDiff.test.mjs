// An AI's change drawn in its note as tracked changes (editor/mdDiff.js):
// the merged source, and what the note's own renderer makes of it with
// rehypeDiffMarks between the sanitizer and KaTeX, as BlockMarkdown runs it.
import assert from "node:assert/strict";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import raw from "rehype-raw";
import katex from "rehype-katex";
import breaks from "remark-breaks";
import gfm from "remark-gfm";
import math from "remark-math";
import { DEL, DEL_END, INS, INS_END, diffMarkdown, marksChange, rehypeDiffMarks, wordsOf } from "../src/editor/mdDiff.js";
import { rehypeNoteSanitize } from "../src/editor/noteSanitize.js";
import { remarkCallouts } from "../src/editor/callouts.js";
import { remarkDiagrams } from "../src/shared/lib/diagramMarkdown.js";

// The marks as text a failure message can show: {+ came +}, [- went -].
const shown = (s) => s.replaceAll(INS, "{+").replaceAll(INS_END, "+}").replaceAll(DEL, "[-").replaceAll(DEL_END, "-]");
const merged = (a, b) => shown(diffMarkdown(a, b));
const render = (text) => renderToStaticMarkup(React.createElement(Markdown, {
  remarkPlugins: [gfm, math, remarkCallouts, breaks, remarkDiagrams],
  rehypePlugins: [raw, rehypeNoteSanitize, rehypeDiffMarks, katex],
}, text));
const drawn = (a, b) => render(diffMarkdown(a, b));
const DEL_RE = /<del class="mdDiff">([^<]*)<\/del>/g;
const INS_RE = /<ins class="mdDiff">([^<]*)<\/ins>/g;
const went = (html) => [...html.matchAll(DEL_RE)].map((m) => m[1]);
const came = (html) => [...html.matchAll(INS_RE)].map((m) => m[1]);

test("a changed word is struck out where it stood, the new one tinted beside it", () => {
  assert.equal(merged("The cavity has a Q of 1e6.", "The cavity had a Q of 1e6."), "The cavity [-has-]{+had+} a Q of 1e6.");
  const html = drawn("The cavity has a Q of 1e6.", "The cavity had a Q of 1e6.");
  assert.equal(html, '<p>The cavity <del class="mdDiff">has</del><ins class="mdDiff">had</ins> a Q of 1e6.</p>');
  // CJK text compares character by character, as the approval card does.
  assert.equal(merged("这是旧的笔记", "这是新的笔记"), "这是[-旧-]{+新+}的笔记");
  // A rewritten phrase reads as one change, not word by word.
  assert.equal(merged("The cat sat on the mat", "A dog ran across the mat"), "[-The cat sat on-]{+A dog ran across+} the mat");
});

test("nothing changed is nothing marked, and marks a note holds are not taken for ours", () => {
  assert.equal(diffMarkdown("same\n- list", "same\n- list"), "same\n- list");
  assert.equal(marksChange(diffMarkdown("a", "a")), false);
  assert.equal(marksChange(diffMarkdown("a", "b")), true);
  assert.equal(diffMarkdown(`odd ${INS}mark`, `odd ${INS}mark`), "odd mark");
});

test("the new text's structure stands: lines and items that went or came keep their markers", () => {
  assert.equal(merged("Draft summary", "Draft summary\nThe key result."), "Draft summary\n{+The key result.+}");
  assert.equal(merged("- a\n- b\n- c", "- a\n- c\n- d"), "- a\n- [-b-]\n- c\n- {+d+}");
  const list = drawn("- a\n- b\n- c", "- a\n- c\n- d");
  assert.deepEqual([went(list), came(list)], [["b"], ["d"]]);
  assert.match(list, /<li><del class="mdDiff">b<\/del><\/li>/);
  // A paragraph that went keeps its own paragraph.
  assert.equal(merged("A\n\nB\n\nC", "A\n\nC"), "A\n\n[-B-]\n\nC");
  // A blank line that went between kept lines leaves the new layout alone.
  assert.equal(diffMarkdown("A\n\nB", "A\nB"), "A\nB");
  // A heading keeps its hashes, a quote its marks, a callout its type.
  assert.equal(merged("## Old title", "## New title"), "## [-Old-]{+New+} title");
  assert.match(drawn("## Old title", "## New title"), /^<h2><del class="mdDiff">Old<\/del><ins class="mdDiff">New<\/ins> title<\/h2>$/);
  assert.equal(merged("> [!note] Title\n> body text", "> [!note] Title\n> new body text"), "> [!note] Title\n> {+new+} body text");
  assert.match(drawn("> [!note] Title\n> body text", "> [!note] Title\n> new body text"), /class="callout callout-note"/);
  // Another heading level, or a ticked task, is the old line out and the new one in.
  assert.equal(merged("## Heading", "### Heading"), "## [-Heading-]\n### {+Heading+}");
  assert.equal(merged("- [ ] task one\n- [ ] task two", "- [x] task one\n- [ ] task two"),
    "- [ ] [-task one-]\n- [x] {+task one+}\n- [ ] task two");
});

test("emphasis the new text opened still closes: what went loses its marks, nothing more", () => {
  assert.equal(merged("**very important** text", "**very crucial** text"), "**very [-important-]{+crucial**+} text");
  assert.equal(drawn("**very important** text", "**very crucial** text"),
    '<p><strong>very <del class="mdDiff">important</del><ins class="mdDiff">crucial</ins></strong> text</p>');
  assert.equal(drawn("_emph start_ end", "_emph begin_ end"),
    '<p><em>emph <del class="mdDiff">start</del><ins class="mdDiff">begin</ins></em> end</p>');
  // Deleted text is plain: its stray markdown punctuation is escaped and its
  // HTML tags go, so a lone $ or ` cannot pair with the new text's.
  assert.equal(merged("price $5 and more", "price $6 and more"), "price [-\\$5-]{+$6+} and more");
  assert.deepEqual(came(drawn("price $5 and more", "price $6 and more")), ["$6"]);
  assert.equal(merged("Keep <u>this</u> word here", "Keep here"), "Keep [-this word-] here");
  // The user's own strikethrough stays a plain <del>.
  assert.match(drawn("a ~~struck~~ word", "a ~~struck~~ term"), /<del>struck<\/del> <del class="mdDiff">word<\/del>/);
});

test("a formula, a code span, a link and a picture change whole, set by their own renderer", () => {
  // Marks sit inside a formula's dollars; two formulas side by side stay two.
  assert.equal(merged("Energy $E = mc$ here", "Energy $E = mc^2$ here"), "Energy $[-E = mc-]$ $" + "{+E = mc^2+}$ here");
  const formula = drawn("Energy $E = mc$ here", "Energy $E = mc^2$ here");
  assert.match(formula, /<del class="mdDiff"><span class="katex">/);
  assert.match(formula, /<ins class="mdDiff"><span class="katex">/);
  assert.doesNotMatch(formula, /katex-error|⸂|⸄/);
  assert.match(drawn("`code` stays", "`code2` stays"), /<del class="mdDiff"><code>code<\/code><\/del><ins class="mdDiff"><code>code2<\/code><\/ins>/);
  assert.match(drawn("see [a](https://a.org) now", "see [b](https://b.org) now"),
    /<del class="mdDiff"><a href="https:\/\/a.org">a<\/a><\/del><ins class="mdDiff"><a href="https:\/\/b.org">b<\/a><\/ins>/);
  // A bare address beside a mark still links (GFM links one only after a space).
  assert.match(drawn("see https://a.org now", "see https://b.org now"), /<ins class="mdDiff"><a href="https:\/\/b.org">/);
  assert.match(drawn("See ![pic](/api/uploads/a.png)", "See ![pic](/api/uploads/b.png)"),
    /<del class="mdDiff"><img src="\/api\/uploads\/a.png" alt="pic"\/><\/del><ins class="mdDiff"><img src="\/api\/uploads\/b.png"/);
  // Each of them is one word to the comparison.
  assert.deepEqual(wordsOf("a $x + y$ `c d` [l m](u) @alice <u>").filter((w) => w.k !== "space").map((w) => w.k),
    ["word", "math", "code", "atom", "mention", "tag"]);
});

test("a code block, a display formula and a table change as one unit, marked whole", () => {
  assert.equal(merged("```js\nlet x = 1;\n```", "```js\nlet x = 2;\n```"), "```js\n[-let x = 1;\n```\n```js\n{+let x = 2;\n```");
  const code = drawn("```js\nlet x = 1;\n```", "```js\nlet x = 2;\n```");
  assert.match(code, /<div class="mdDiffBlock del"><pre><code class="language-js">let x = 1;\n<\/code><\/pre><\/div>/);
  assert.match(code, /<div class="mdDiffBlock ins"><pre><code class="language-js">let x = 2;\n<\/code><\/pre><\/div>/);
  // Inside a list item, the mark goes past the item's indent: the fence stays in it.
  assert.match(drawn("- item\n  ```py\n  x = 1\n  ```", "- item\n  ```py\n  x = 2\n  ```"),
    /^<ul>\n<li>item\n<div class="mdDiffBlock del"><pre>.*<\/pre><\/div>\n<div class="mdDiffBlock ins"><pre>/s);
  // A blank first line of code is passed over: a mark there would end the item.
  assert.equal(merged("- item\n  ```\n\n  x = 1\n  ```", "- item\n  ```\n\n  x = 2\n  ```"),
    "- item\n  ```\n\n  [-x = 1\n  ```\n  ```\n\n  {+x = 2\n  ```");
  assert.equal((drawn("- item\n  ```\n\n  x = 1\n  ```", "- item\n  ```\n\n  x = 2\n  ```").match(/<li>/g) || []).length, 1);
  // A display formula's marks go on its own lines, never on its $$ lines.
  assert.equal(merged("Intro\n\n$$\nx^2\n$$\n\nEnd", "Intro\n\n$$\ny^2\n$$\n\nEnd"),
    "Intro\n\n$$\n[-x^2-]\n$$\n$$\n{+y^2+}\n$$\n\nEnd");
  const display = drawn("Intro\n\n$$\nx^2\n$$\n\nEnd", "Intro\n\n$$\ny^2\n$$\n\nEnd");
  assert.match(display, /<div class="mdDiffBlock del"><span class="katex-display">/);
  assert.match(display, /<div class="mdDiffBlock ins"><span class="katex-display">/);
  assert.match(display, /<p>End<\/p>$/);
  assert.match(drawn("> quote\n> $$\n> a\n> $$", "> quote\n> $$\n> b\n> $$"), /^<blockquote>\n<p>quote<\/p>\n<div class="mdDiffBlock del">/);
  // Two tables stay two, each cell of the one that went struck out.
  const tables = drawn("| a | b |\n|---|---|\n| 1 | 2 |", "| a | b |\n|---|---|\n| 1 | 3 |");
  assert.equal((tables.match(/<table>/g) || []).length, 2);
  assert.deepEqual(went(tables), ["a", "b", "1", "2"]);
  assert.deepEqual(came(tables), ["a", "b", "1", "3"]);
  assert.equal((drawn("text\n| a |\n|---|\n| 1 |", "words\n| a |\n|---|\n| 2 |").match(/<table>/g) || []).length, 2);
});

test("a long note with one change costs that change", () => {
  const para = (i) => `Paragraph ${i}: the qubit's T1 is limited by $\\Gamma_1 = 1/T_1$, see [[ref${i}]].`;
  const before = Array.from({ length: 400 }, (_, i) => para(i)).join("\n\n");
  const after = before.replace("Paragraph 200:", "Paragraph two hundred:");
  const started = performance.now();
  const out = shown(diffMarkdown(before, after));
  assert.ok(performance.now() - started < 1000, "well under a second");
  assert.equal(out.match(/\[-|\{\+/g).length, 2);
  assert.match(out, /Paragraph \[-200:-\]\{\+two hundred:\+\} the qubit/);
});
