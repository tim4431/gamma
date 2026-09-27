// The chat's context chips preview a note's source as plain words with the
// math apart (chat/chipText.js): what the chip shows instead of raw markdown.
import assert from "node:assert/strict";
import { test } from "node:test";
import { chipSegments, stripMarkdown } from "../src/chat/chipText.js";

test("markdown marks go, the words stay", () => {
  assert.equal(stripMarkdown("**bold** and *italic*, ==marked== ~~gone~~ `code`"), "bold and italic, marked gone code");
  assert.equal(stripMarkdown("## Heading"), "Heading");
  assert.equal(stripMarkdown("> [!note] A callout"), "A callout");
  assert.equal(stripMarkdown("- [x] done item"), "done item");
  assert.equal(stripMarkdown("see [the paper](https://example.org) and [[Other page]]"), "see the paper and Other page");
  assert.equal(stripMarkdown("![a figure|300](/api/uploads/x.png) caption"), "a figure caption");
  assert.equal(stripMarkdown('<span style="color:red">red words</span>'), "red words");
  assert.equal(stripMarkdown("snake_case_name stays"), "snake_case_name stays");
});

test("math becomes its own segment, display math included", () => {
  assert.deepEqual(chipSegments("Scaling by $\\sqrt{d_k}$ keeps the **variance** near 1"), [
    { text: "Scaling by " }, { math: "\\sqrt{d_k}" }, { text: " keeps the variance near 1" },
  ]);
  assert.deepEqual(chipSegments("$$\nE = mc^2\n$$"), [{ math: "E = mc^2" }]);
  // What the note renders as math is math here too; an escaped \$ is not.
  assert.deepEqual(chipSegments("costs \\$5 and \\$3"), [{ text: "costs $5 and $3" }]);
});

test("whitespace collapses to one line", () => {
  assert.deepEqual(chipSegments("  first line\n\n  second   line  "), [{ text: "first line second line" }]);
  assert.deepEqual(chipSegments(""), []);
});
