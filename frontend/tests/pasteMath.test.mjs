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
  for (const c of CASES.dollar) assert.equal(dollarMath(c.in, c.keep), c.out, c.why);
});

test("converting twice changes nothing more", () => {
  for (const c of CASES.dollar) assert.equal(dollarMath(c.out, c.keep), c.out, c.why);
});

test("the shared cases: a paste into source stays as copied", () => {
  for (const c of CASES.paste) assert.equal(pasteMath(c.at.replace("|", ""), c.at.indexOf("|"), c.text), c.out, c.why);
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
