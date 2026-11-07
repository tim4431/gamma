// Pasted \( \) and \[ \] math as the $ and $$ notes render
// (editor/pasteMath.js). The cases in tests/shared/pastemath.json are the
// backend's too (backend/tests/test_paste_math.py), so a formula the AI
// writes and one a person pastes go in alike.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { dollarMath, pasteMath } from "../src/editor/pasteMath.js";
import { mathTex, scanMathSpans } from "../src/editor/mdScan.js";

const CASES = JSON.parse(readFileSync(new URL("../../tests/shared/pastemath.json", import.meta.url), "utf8"));

test("the shared cases: formulas convert, escaped brackets and code stay", () => {
  for (const c of CASES) assert.equal(dollarMath(c.in, c.keep), c.out, c.why);
});

test("converting twice changes nothing more", () => {
  for (const c of CASES) assert.equal(dollarMath(c.out, c.keep), c.out, c.why);
});

test("a converted formula is the span the editor and the rendered view read", () => {
  const spans = (text) => scanMathSpans(text).map((s) => [text.slice(s.from, s.to), s.display]);
  assert.deepEqual(spans(dollarMath("For $5 you get \\(x\\) and \\(y^2\\).")), [["$x$", false], ["$y^2$", false]]);
  assert.deepEqual(spans(dollarMath("Energy:\n\\[\nE = mc^2\n\\]\nwhere \\( m \\) is the mass")),
    [["$$\nE = mc^2\n$$", true], ["$m$", false]]);
});

test("a converted formula in a quote is TeX without the quote's marks", () => {
  const text = dollarMath("> \\[\n> E = mc^2\n> \\]");
  const [span] = scanMathSpans(text);
  assert.equal(mathTex(text, span), "\nE = mc^2\n");
  assert.equal(mathTex("$$\na\n> b\n$$", scanMathSpans("$$\na\n> b\n$$")[0]), "\na\n> b\n", "outside a quote a > is TeX");
});

// The same positions as backend/tests/test_paste_math.py's.
test("a paste into source stays as copied", () => {
  const at = (withCaret, text) => pasteMath(withCaret.replace("|", ""), withCaret.indexOf("|"), text);
  assert.equal(at("see |", "\\(x\\)"), "$x$");
  assert.equal(at("$a + | b$", "\\(x\\)"), "\\(x\\)", "inside inline math");
  assert.equal(at("$$\na |\n$$", "\\[x^2\\]"), "\\[x^2\\]", "inside display math");
  assert.equal(at("price $5 and |", "\\(x\\)"), "\\(x\\)", "an unclosed $ reads as math to its line's end");
  assert.equal(at("```\nconst s = |\n```", "\\(x\\)"), "\\(x\\)", "inside a code fence");
  assert.equal(at("```\nopen |", "\\(x\\)"), "\\(x\\)", "inside an open fence");
  assert.equal(at("```py|thon", "\\(x\\)"), "$x$", "in an open fence's info string");
  assert.equal(at("> ```latex\n> |\n> ```", "\\(x\\)"), "\\(x\\)", "inside a quoted fence");
  assert.equal(at("- step\n  ~~~\n  |\n  ~~~", "\\(x\\)"), "\\(x\\)", "inside a listed tilde fence");
  assert.equal(at("- ```\n  ab\n  |```", "\\(x\\)"), "\\(x\\)", "before an indented closing mark");
  assert.equal(at("```\ncode\n```\nafter |", "\\(x\\)"), "$x$", "after a fence");
  assert.equal(at("- ```\n  code\nafter |", "\\(x\\)"), "$x$", "after the list item that held a fence");
  assert.equal(at("> ```\n> code\n\nafter |", "\\(x\\)"), "$x$", "after the quote that held one");
  assert.equal(at("use `$$` for |", "\\(x\\)"), "$x$", "a $$ in code is no math");
  assert.equal(at("Costs $5 a month.\n\nSet it up: |\n\n```sh\necho $HOME\n```", "\\(x\\)"), "$x$", "a dollar in code closes no dollar above");
  assert.equal(at("$$\na `x$y` b\n|\n$$", "\\(x\\)"), "\\(x\\)", "nor a $$ block");
  assert.equal(at("run `echo |` now", "\\(x\\)"), "\\(x\\)", "inside inline code");
  assert.equal(at("`code` and |", "\\(x\\)"), "$x$", "after inline code");
  assert.equal(at("Set `$HOME` first, then |", "\\(x\\)"), "$x$", "a dollar in code is no math");
});
