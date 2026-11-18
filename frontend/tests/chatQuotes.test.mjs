import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_CHAT_QUOTES, QUOTE_MAX, addQuote, noteTitle, quoteMarkdown, quoteOf, splitQuoted, structuredNote, withChatQuotes }
  from "../src/chat/chatQuotes.js";

test("a quote is every line behind \"> \", its blank lines a bare \">\"", () => {
  assert.equal(quoteMarkdown("one\n\ntwo"), "> one\n>\n> two");
  assert.equal(withChatQuotes("why?", ["a", "b\nc"]), "> a\n\n> b\n> c\n\nwhy?");
  assert.equal(withChatQuotes("why?", []), "why?");
});

test("a selection becomes one quote: trimmed, blank runs folded, cut at its cap", () => {
  assert.equal(quoteOf("  a\r\n\n\n\nb  "), "a\n\nb");
  const long = quoteOf("x".repeat(QUOTE_MAX + 50));
  assert.equal(long.length, QUOTE_MAX + 1);
  assert.ok(long.endsWith("…"));
});

test("a passage quoted twice is one quote, and past the cap none is added", () => {
  let quotes = [];
  ({ quotes } = addQuote(quotes, "same"));
  ({ quotes } = addQuote(quotes, " same "));
  assert.deepEqual(quotes, ["same"]);
  const full = Array.from({ length: MAX_CHAT_QUOTES }, (_, i) => `q${i}`);
  assert.deepEqual(addQuote(full, "one more"), { quotes: full, full: true });
  assert.deepEqual(addQuote([], "   ").quotes, []);
});

test("a sent message's quotes are drawn apart from its words, ahead of them or after", () => {
  assert.deepEqual(splitQuoted("> It is $x$.\n>\n> - a point\n\nwhy?"), [
    { quote: true, text: "It is $x$.\n\n- a point" },
    { quote: false, text: "why?" },
  ]);
  assert.deepEqual(splitQuoted("focus\n\n> start of the passage…"), [
    { quote: false, text: "focus" },
    { quote: true, text: "start of the passage…" },
  ]);
  assert.deepEqual(splitQuoted("no quote here"), [{ quote: false, text: "no quote here" }]);
});

test("a note's page is named by its first line, its marks dropped, cut at a word", () => {
  assert.equal(noteTitle("## **Noise** model\n\ntext"), "Noise model");
  assert.equal(noteTitle("- [ ] check $T_1$ against [the paper](/?page=x)"), "check T_1 against the paper");
  assert.equal(noteTitle("\n```\ncode\n```"), "code");
  const title = noteTitle(`${"word ".repeat(30)}end`);
  assert.ok(title.length <= 81 && title.endsWith("…"), title);
});

test("the structured note is the answer, a fence around the whole of it taken off", () => {
  assert.equal(structuredNote("```markdown\n# Title\n- a\n```"), "# Title\n- a");
  assert.equal(structuredNote("  # Title\n\n- a  "), "# Title\n\n- a");
  assert.equal(structuredNote(undefined), "");
});
