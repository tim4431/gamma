// The workspace search's result text (search/snippets.js): markdown to a
// plain line — the one rule, also the [[ picker's rows and the chat chip's
// words — and query matches mapped back through the normalized view.
import assert from "node:assert/strict";
import { test } from "node:test";
import { markedParts, matchRanges, plainSnippet } from "../src/search/snippets.js";

test("plainSnippet drops the markdown a reader never sees", () => {
  assert.equal(plainSnippet("> [!note] Idea\n> Compare the attention window."), "Idea Compare the attention window.");
  assert.equal(plainSnippet("## Week 38 — attention experiments"), "Week 38 — attention experiments");
  assert.equal(plainSnippet("See [[Attention Is All You Need]] section 3.2"), "See Attention Is All You Need section 3.2");
  assert.equal(plainSnippet("[[Target|the alias]] and ![[Embedded]]"), "the alias and Embedded");
  assert.equal(plainSnippet("**bold**, *it*, ~~gone~~, ==lit== and `code`"), "bold, it, gone, lit and code");
  assert.equal(plainSnippet("- [x] done item\n1. first"), "done item first");
  assert.equal(plainSnippet("Energy $E = mc^2$ and $$\\int f$$"), "Energy E = mc^2 and \\int f");
  assert.equal(plainSnippet("a [link](https://x.org) and ![fig|300](/api/uploads/a.png)"), "a link and fig");
  assert.equal(plainSnippet('<span style="color:red">red</span> text'), "red text");
});

test("plainSnippet leaves ordinary punctuation alone", () => {
  assert.equal(plainSnippet("snake_case_name and 2 * 3 * 4"), "snake_case_name and 2 * 3 * 4");
  assert.equal(plainSnippet("costs \\$5 or $6"), "costs \\$5 or $6");
});

test("plainSnippet: nested quotes, image sizes, aliased embeds, any inline tag", () => {
  assert.equal(plainSnippet("> > [!warning]- Folded\n> > nested line"), "Folded nested line");
  assert.equal(plainSnippet("> - quoted item\n> ## quoted heading"), "quoted item quoted heading");
  assert.equal(plainSnippet("      - [ ] deep item"), "deep item");
  assert.equal(plainSnippet("![[Target|shown]] and ![[Plain]]"), "shown and Plain");
  assert.equal(plainSnippet("![old](/api/uploads/a.png){:width 300} and ![wide|300x200](/api/uploads/b.png)"), "old and wide");
  assert.equal(plainSnippet("<mark>kept</mark> a<br>b <u>under</u>"), "kept a b under");
  assert.equal(plainSnippet("**bold across\nlines** and *a **b** c*"), "bold across lines and a b c");
  assert.equal(plainSnippet("a < b and c > d, $x<y$"), "a < b and c > d, x<y");
});

// The chat chip's prose runs (chat/chipText.js): the math is cut out first,
// so a $ left is literal and an escaped \$ reads as $; the run keeps the
// spaces at its ends (a formula sits beside them).
test("plainSnippet with mathApart: a chat chip's prose run", () => {
  const chip = (src) => plainSnippet(src, { mathApart: true });
  assert.equal(chip("**bold** and *italic*, ==marked== ~~gone~~ `code`"), "bold and italic, marked gone code");
  assert.equal(chip("## Heading"), "Heading");
  assert.equal(chip("> [!note] A callout"), "A callout");
  assert.equal(chip("- [x] done item"), "done item");
  assert.equal(chip("see [the paper](https://example.org) and [[Other page]]"), "see the paper and Other page");
  assert.equal(chip("![a figure|300](/api/uploads/x.png) caption"), "a figure caption");
  assert.equal(chip('<span style="color:red">red words</span>'), "red words");
  assert.equal(chip("snake_case_name stays"), "snake_case_name stays");
  assert.equal(chip("costs \\$5 or $6"), "costs $5 or $6");
  assert.equal(chip(" keeps the  **variance** "), " keeps the variance ");
});

test("matchRanges finds the phrase, case-insensitively by default", () => {
  assert.deepEqual(matchRanges("Self-Attention and attention", "attention"), [[5, 14], [19, 28]]);
  assert.deepEqual(matchRanges("Self-Attention", "attention", { caseSensitive: true }), []);
});

test("matchRanges maps normalized matches back to the source characters", () => {
  // digit separators: "3000" finds "3,000"
  assert.deepEqual(matchRanges("a 3,000-qubit array", "3000"), [[2, 7]]);
  // a ligature: "fi" finds the one ﬁ character
  const text = "eﬃcient ﬁlter";
  const [r] = matchRanges(text, "filter");
  assert.equal(text.slice(r[0], r[1]), "ﬁlter");
  // a hyphenated line break: "system" finds "sys-\ntem"
  const broken = "the sys-\ntem works";
  const [b] = matchRanges(broken, "system");
  assert.equal(broken.slice(b[0], b[1]), "sys-\ntem");
});

test("matchRanges falls back to the terms when the phrase is absent", () => {
  assert.deepEqual(matchRanges("attention is what we need", "need attention"), [[0, 9], [21, 25]]);
  assert.deepEqual(matchRanges("nothing here", "missing words"), []);
  assert.deepEqual(matchRanges("anything", ""), []);
});

test("markedParts splits text around the marks and leads into a far match", () => {
  assert.deepEqual(markedParts("see Attention here", "attention"), [
    { text: "see ", mark: false }, { text: "Attention", mark: true }, { text: " here", mark: false },
  ]);
  const long = `${"word ".repeat(30)}target end`;
  const parts = markedParts(long, "target", {}, 40);
  assert.ok(parts[0].text.startsWith("…"), "a leading ellipsis");
  assert.ok(parts[0].text.length < 40, "the line starts near the match");
  assert.deepEqual(parts.slice(1), [{ text: "target", mark: true }, { text: " end", mark: false }]);
  assert.deepEqual(markedParts("no match", "zzz"), [{ text: "no match", mark: false }]);
});
