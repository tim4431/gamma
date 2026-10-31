// node --test tests/textBoxShown.test.mjs (from frontend/) — what a text
// box's read mode renders: a Zotero box shows the text Zotero is sent, its
// blank lines each a line, as in Zotero's textarea and in gamma/text_box.py's
// layout of a Zotero box. tests/shared/zoteroshown.json is read by
// backend/tests/test_text_box_shown.py too.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { scanMathSpans } from "../src/editor/mdScan.js";
import { expandBlankLines } from "../src/editor/mdMarks.js";
import { LINE, shownText } from "../src/markup/textBox.js";

const ZOTERO = { x: 0, y: 0, w: 90, h: 40, auto: false, size: 12, color: "#1f1f1f", bg: null, zotero: true };
const GAMMA = { x: 0, y: 0, w: 90, h: 40, auto: false, size: 12, color: "#1f1f1f", bg: null };
const NBSP = " ";
// The lines the renderer shows for plain text without wrapping (remark-breaks:
// a line per source line; expandBlankLines: a blank line after a run's first).
const shownLines = (md) => expandBlankLines(md).split("\n").filter((l) => l.trim() !== "" || l.includes(NBSP)).length;

test("a Zotero box's run of blank lines starts with a paragraph holding a no-break space; a Gamma box's text is as written", () => {
  assert.equal(shownText("a\n\nb", ZOTERO), `a\n\n${NBSP}\n\nb`);
  assert.equal(shownText("a\n\n\nb", ZOTERO), `a\n\n${NBSP}\n\n\nb`);
  assert.equal(shownText("a\r\n\r\nb", ZOTERO), `a\n\n${NBSP}\n\nb`);
  assert.equal(shownText("a\n   \nb", ZOTERO), `a\n\n${NBSP}\n\nb`, "a line of spaces is blank, and empty");
  assert.equal(shownText("a\n\u3000\nb", ZOTERO), `a\n\n${NBSP}\n\nb`, "so is a line of a full-width space");
  assert.equal(shownText("\n\na\n\n", ZOTERO), "a", "blank lines at either end go");
  assert.equal(shownText(" \n\u3000\n", ZOTERO), "");
  for (const md of ["a\n\nb", "plain", "from zotero\nonline edit", "a\n", "a\n\u3000\nb", "see https://example.org"]) {
    assert.equal(shownText(md, GAMMA), md);
    assert.equal(shownText(md, null), md);
  }
  assert.equal(shownText("from zotero\nonline edit", ZOTERO), "from zotero\nonline edit", "no blank line, no change");
  assert.equal(shownText("", ZOTERO), "");
});

test("each blank line of a Zotero box is a line shown; a Gamma box keeps the renderer's paragraph break", () => {
  for (const [md, zotero, gamma] of [["a\nb", 2, 2], ["a\n\nb", 3, 2], ["a\n\n\nb", 4, 3], ["a\n\nb\n\n\n\nc", 7, 5]]) {
    assert.equal(shownLines(shownText(md, ZOTERO)), zotero, JSON.stringify(md));
    assert.equal(shownLines(shownText(md, GAMMA)), gamma, JSON.stringify(md));
  }
});

test("a blank line inside fenced code or display math stays as it is, as the server's layout reads it", () => {
  assert.equal(shownText("x\n```\na\n\nb\n```\n\ny", ZOTERO), `x\n\`\`\`\na\n\nb\n\`\`\`\n\n${NBSP}\n\ny`);
  assert.equal(shownText("$$\na\n\nb\n$$\n\nc", ZOTERO), `$$\na\n\nb\n$$\n\n${NBSP}\n\nc`);
  assert.equal(shownText("$$x$$\n\nc", ZOTERO), `$$x$$\n\n${NBSP}\n\nc`, "one-line math ends on its line");
  assert.equal(shownText("```\nopen\n\nfence", ZOTERO), "```\nopen\n\nfence", "an unclosed fence runs to the end");
  assert.equal(shownText("~~~\na\n\nb\n~~~\n\nc", ZOTERO), `~~~\na\n\nb\n~~~\n\n${NBSP}\n\nc`, "a ~~~ fence is code too");
  assert.equal(shownText("- ```\n  a\n\n  b\n  ```", ZOTERO), "- ```\n  a\n\n  b\n  ```", "so is a fence in a list item");
});

test("a bare URL in a Zotero box is split by an empty span, outside code, math and link markup", () => {
  assert.equal(shownText("see https://example.org/a now", ZOTERO), "see https<span></span>://example.org/a now");
  assert.equal(shownText("WWW.example.org and http://x.org", ZOTERO), "WWW<span></span>.example.org and http<span></span>://x.org");
  assert.equal(shownText("<https://example.org>", ZOTERO), "<https<span></span>://example.org>");
  assert.equal(shownText("[https://example.org](https://example.org/a)", ZOTERO), "https<span></span>://example.org");
  assert.equal(shownText("`[https://x](https://x)`", ZOTERO), "`[https://x](https://x)`");
  for (const md of ["[label](https://example.org/a)", "![pic](https://example.org/a.png)", "`https://example.org`",
    "$https://x$", '<a href="https://example.org">x</a>', "[ref]: https://example.org", "```\nhttps://example.org\n```"]) {
    assert.equal(shownText(md, ZOTERO), md, md);
  }
});

// Read mode's pipeline without BlockTree's other rewrites: blank runs shown
// outside math (mdPreprocess), then the renderer's plugins. Each line shown
// is a paragraph or a line break; a line holding only a no-break space is
// an empty line of the text.
const render = (md) => {
  let text = "", pos = 0;
  for (const s of scanMathSpans(md)) { text += expandBlankLines(md.slice(pos, s.from)) + md.slice(s.from, s.to); pos = s.to; }
  text += expandBlankLines(md.slice(pos));
  return renderToStaticMarkup(React.createElement(ReactMarkdown, {
    remarkPlugins: [remarkGfm, remarkMath, remarkBreaks], rehypePlugins: [rehypeRaw], children: text,
  }));
};
const linesOf = (html) => html.replace(/<br\s*\/?>\n?/g, "\n").replace(/<\/p>\s*<p>/g, "\n").replace(/<[^>]+>/g, "")
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/\n$/, "")
  .split("\n").map((l) => (l.replace(/\u00a0/g, "").trim() ? l : ""));

const ZOTERO_SHOWN = JSON.parse(readFileSync(new URL("../../tests/shared/zoteroshown.json", import.meta.url), "utf8"));
for (const c of ZOTERO_SHOWN.cases) {
  test(`read mode shows what Zotero is sent: ${c.note}`, () => {
    const html = render(shownText(c.md, ZOTERO_SHOWN.box));
    const lines = linesOf(html);
    assert.equal(lines.length, c.lines, html);
    assert.equal(lines.join("\n"), c.sent, html);
    assert.doesNotMatch(html, /<a [^>]*>(https?:|www\.)/i, "no link reads as a URL, which its chip would show as a page title");
  });
}

test("markup.css draws a link, a [[ref]] and a citation in a Zotero box as its text", () => {
  const css = readFileSync(new URL("../src/markup/markup.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = css.match(/\.textBox\.zotero \.textBoxText :is\(([^)]*)\) \{([^}]*)\}/);
  assert.ok(rule, "a rule for the chips in a Zotero box");
  for (const chip of [".linkChip", ".gammaLinkCard", ".citePill", ".blockRefChip", ".unlinkedRef"]) assert.ok(rule[1].includes(chip), chip);
  const decl = Object.fromEntries(rule[2].split(";").map((d) => d.split(":").map((v) => v.trim())).filter((d) => d.length > 1));
  assert.deepEqual([decl.display, decl.padding, decl.margin, decl.border, decl.font, decl["white-space"]],
    ["inline", "0", "0", "0", "inherit", "inherit"]);
  assert.match(css, /\.textBox\.zotero \.textBoxText :is\(\.linkChip, \.gammaLinkCard\) :is\(svg, img\),[^{]*\{\s*display: none;/);
});

// The layouts of tests/shared/zoterotext.json without wrapping: each line
// shown is a row of size × LINE in gamma/text_box.py's layout of the
// Zotero box.
const ZOTERO_TEXT = JSON.parse(readFileSync(new URL("../../tests/shared/zoterotext.json", import.meta.url), "utf8"));
for (const c of ZOTERO_TEXT.layout.filter((l) => l.md.includes("\n\n"))) {
  test(`shown as the server lays a Zotero box out: ${JSON.stringify(c.md)}`, () => {
    assert.equal(shownLines(shownText(c.md, { ...c.box, zotero: true })) * c.box.size * LINE, c.zotero.size[1]);
  });
}
