// The links of a note's text as the link card edits them (editor/linkEdit.js):
// where each is, the Markdown a change writes, and a paste over words.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  applyLinkChange, linkKey, linkMarkdown, linkSpanAt, linkSpans, nthLinkSpan, pastedLink, spanWords,
} from "../src/editor/linkEdit.js";
import { tablePipe } from "../src/editor/mdScan.js";

const kinds = (text) => linkSpans(text).map((s) => [s.kind, text.slice(s.from, s.to)]);

test("every link of a note in order; none in code, math, an image or an embed", () => {
  const text = "see [[a1]], [[a1|the plan]], [paper](https://x.org/p) and https://y.org/q. ![pic](https://z.org/i.png) "
    + "![[a1]] `[[a2]] https://c.org` $[x](y)$ <https://w.org/a b> <https://w.org/ab>";
  assert.deepEqual(kinds(text), [
    ["ref", "[[a1]]"], ["ref", "[[a1|the plan]]"], ["md", "[paper](https://x.org/p)"], ["bare", "https://y.org/q"],
    ["bare", "https://w.org/a"], ["bare", "<https://w.org/ab>"],
  ]);
  const [, plan, md] = linkSpans(text);
  assert.equal(plan.words, "the plan");
  assert.equal(md.href, "https://x.org/p");
});

test("a bare address ends as GFM ends it: a sentence's punctuation and an unopened ) left out", () => {
  assert.deepEqual(kinds("(see https://en.wikipedia.org/wiki/Foo_(bar)) and https://a.org/x?, ok"),
    [["bare", "https://en.wikipedia.org/wiki/Foo_(bar)"], ["bare", "https://a.org/x"]]);
  assert.deepEqual(kinds("攻略：https://a.org/v and 1https://b.org but xhttps://c.org"),
    [["bare", "https://a.org/v"], ["bare", "https://b.org"]], "after anything but a letter, as the rendered view links one");
  assert.deepEqual(kinds("[t](<https://a.org/a b>)"), [["md", "[t](<https://a.org/a b>)"]]);
  assert.equal(linkSpans("[t](<https://a.org/a b>)")[0].href, "https://a.org/a b");
});

test("the link at the caret, its ends included", () => {
  const text = "go [[a1|there]] now";
  assert.equal(linkSpanAt(text, 3)?.id, "a1");
  assert.equal(linkSpanAt(text, 15)?.id, "a1");
  assert.equal(linkSpanAt(text, 16), null);
});

test("the nth rendered link of a target is the nth span of it", () => {
  const text = "[a](https://x.org) [[p1]] [b](https://x.org) https://x.org [[p1|again]]";
  const tr = (u) => `T(${u})`;
  assert.equal(linkKey(linkSpans(text)[0], tr), "url:T(https://x.org)");
  assert.equal(text.slice(...Object.values(nthLinkSpan(text, "url:T(https://x.org)", 2, tr)).slice(0, 2)), "https://x.org");
  assert.equal(nthLinkSpan(text, "ref:p1", 1, tr).words, "again");
  assert.equal(nthLinkSpan(text, "ref:p1", 2, tr), null);
});

test("a link's Markdown: a page link with words, an address with a label or alone", () => {
  assert.equal(linkMarkdown({ kind: "block", id: "p1" }, ""), "[[p1]]");
  assert.equal(linkMarkdown({ kind: "block", id: "p1" }, " the [plan]\nnow "), "[[p1|the plan now]]");
  assert.equal(linkMarkdown({ kind: "block", id: "p1" }, "cell", "\\|"), "[[p1\\|cell]]");
  assert.equal(linkMarkdown({ kind: "url", url: "https://x.org/a" }, "the [draft]"), "[the \\[draft\\]](https://x.org/a)");
  assert.equal(linkMarkdown({ kind: "url", url: "https://x.org/a" }, "C:\\"), "[C:\\\\](https://x.org/a)");
  assert.equal(linkMarkdown({ kind: "url", url: "https://x.org/a b" }, "t"), "[t](<https://x.org/a b>)");
  assert.equal(linkMarkdown({ kind: "url", url: "https://x.org/a" }, ""), "https://x.org/a");
  assert.equal(linkMarkdown({ kind: "url", url: "https://x.org/a." }, ""), "<https://x.org/a.>", "a bare one would lose its dot");
  assert.equal(linkMarkdown({ kind: "url", url: "?block=b1" }, ""), "[?block=b1](?block=b1)");
  for (const md of ["[[p1|the plan now]]", "[the \\[draft\\]](https://x.org/a)", "<https://x.org/a.>", "[t](<https://x.org/a b>)"]) {
    assert.equal(linkSpans(md).length, 1, `${md} reads back as one link`);
    assert.equal(linkSpans(md)[0].to, md.length);
  }
  assert.equal(spanWords(linkSpans("[the \\[draft\\]](https://x.org/a)")[0]), "the [draft]");
});

test("a change keeps the target unless given one, converts between kinds, and removing leaves the words", () => {
  const text = "see [paper](https://x.org/p) and [[p1|plan]] and [[p2]] and https://y.org";
  const [md, words, plain, bare] = linkSpans(text);
  assert.equal(applyLinkChange(text, md, { words: "the paper" }), "see [the paper](https://x.org/p) and [[p1|plan]] and [[p2]] and https://y.org");
  assert.equal(applyLinkChange(text, md, { words: "it", to: { kind: "block", id: "p9" } }), "see [[p9|it]] and [[p1|plan]] and [[p2]] and https://y.org");
  assert.equal(applyLinkChange(text, words, { words: "", to: { kind: "url", url: "https://z.org" } }),
    "see [paper](https://x.org/p) and https://z.org and [[p2]] and https://y.org");
  assert.equal(applyLinkChange(text, words, { remove: true }), "see [paper](https://x.org/p) and plan and [[p2]] and https://y.org");
  assert.equal(applyLinkChange(text, plain, { remove: true, shown: "Page two" }), "see [paper](https://x.org/p) and [[p1|plan]] and Page two and https://y.org");
  assert.equal(applyLinkChange(text, md, { remove: true }), "see paper and [[p1|plan]] and [[p2]] and https://y.org");
  assert.equal(applyLinkChange(text, bare, { remove: true }), null);
  const row = "| a |\n| --- |\n| [[p1]] |";
  assert.equal(applyLinkChange(row, linkSpans(row)[0], { words: "cell" }), "| a |\n| --- |\n| [[p1\\|cell]] |");
  assert.equal(tablePipe("text [[", 7), "|");
});

test("an address pasted over words links them; a copied page link is a page link", () => {
  const origin = "https://gamma.test";
  assert.equal(pastedLink("the paper", " https://arxiv.org/abs/1904.06560 ", origin), "[the paper](https://arxiv.org/abs/1904.06560)");
  assert.equal(pastedLink("the plan", "https://gamma.test/?ws=w1&page=p1", origin), "[[p1|the plan]]");
  assert.equal(pastedLink("that note", "https://gamma.test/?block=b1", origin, "\\|"), "[[b1\\|that note]]");
  assert.equal(pastedLink("p. 3", "https://gamma.test/?page=p1&pdf_page=3&quote=x", origin), "[p. 3](https://gamma.test/?page=p1&pdf_page=3&quote=x)");
  assert.equal(pastedLink("", "https://x.org", origin), null, "no words");
  assert.equal(pastedLink("two\nlines", "https://x.org", origin), null);
  assert.equal(pastedLink("see [[p1]]", "https://x.org", origin), null, "words holding a link");
  assert.equal(pastedLink("words", "https://x.org and more", origin), null, "not one address");
  assert.equal(pastedLink("words", "plain text", origin), null);
});
