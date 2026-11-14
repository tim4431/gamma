// node --test tests/textAnchors.test.mjs (from frontend/) — a range of a
// text file in the code view (src/documents/viewers/textAnchors.js): the
// columns and context by the server's rule (tests/shared/textposition.json,
// which backend/tests/test_shared_fixtures.py runs through
// highlights.span_text), a selection's span, a stored range's offsets, the
// chat's lines and what each highlight is drawn as.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { EditorState } from "@codemirror/state";
import { CONTEXT_CHARS, TEXT_QUOTE_MAX, contextOf, lineCueSpec, lineSpotOf, markSpec, rangeOf, spanOf, toCodePoints,
  toCodeUnits } from "../src/documents/viewers/textAnchors.js";
import { LINE_CAP, MAX_TEXT_BYTES } from "../src/documents/viewers/textLoad.js";
import { textSpan } from "../src/shared/model/blockModel.js";

const FIXTURE = JSON.parse(await readFile(new URL("../../tests/shared/textposition.json", import.meta.url), "utf8"));

const stateOf = (lines) => EditorState.create({ doc: lines.join("\n") });
// The offset of a 1-based line and a column in code units.
const at = (state, line, ch) => state.doc.line(line).from + ch;

test("the fixture's numbers are the module's", () => {
  assert.equal(FIXTURE.context, CONTEXT_CHARS);
  assert.equal(FIXTURE.quote_max, TEXT_QUOTE_MAX);
  assert.equal(FIXTURE.max_text_bytes, MAX_TEXT_BYTES, "the viewer shows whole what a run places ranges in");
});

test("a stored position reads as the server's text_span reads it (the shared fixture)", () => {
  assert.ok(FIXTURE.spans.length >= 10);
  for (const c of FIXTURE.spans) assert.deepEqual(textSpan(c.position), c.span, c.name);
});

test("a selection's context stops short of a line the view cut, whose end is not the file's", () => {
  const cut = `${"x".repeat(LINE_CAP)}…`;
  const state = stateOf([cut, "ab", cut]);
  const got = spanOf(state, at(state, 2, 0), at(state, 2, 2), "h", new Set([1, 3]));
  assert.equal(got.quote, "ab");
  assert.equal(got.position.prefix, "", "nothing of the cut line above");
  assert.equal(got.position.suffix, "", "nor of the one below");
  const near = stateOf([cut, "one", "ab", "two"]);
  const two = spanOf(near, at(near, 3, 0), at(near, 3, 2), "h", new Set([1]));
  assert.equal(two.position.prefix, "one\n");
  assert.equal(two.position.suffix, "\ntwo");
});

test("the chat's lines of a selection that starts at a line's end begin at the next line", () => {
  const state = stateOf(["one", "two", "three", "four"]);
  assert.deepEqual(lineSpotOf(state.doc, at(state, 1, 3), at(state, 3, 5)), { line: 2, end: 3 });
  assert.deepEqual(lineSpotOf(state.doc, at(state, 1, 3), at(state, 2, 0)), { line: 1, end: 1 }, "a line break alone stays its line");
  const got = spanOf(state, at(state, 1, 3), at(state, 3, 5), "h");
  assert.deepEqual([got.position.line, got.position.col, got.position.end, got.quote], [2, 0, 3, "two\nthree"]);
});

test("columns in code points and code units, case for case (the shared fixture)", () => {
  for (const c of FIXTURE.cases) {
    const { span, utf16, lines } = c;
    const first = lines[span.line - 1], last = lines[span.end - 1];
    assert.equal(toCodeUnits(first, span.col), utf16.col, `${c.name}: col`);
    assert.equal(toCodeUnits(last, span.end_col), utf16.end_col, `${c.name}: end_col`);
    assert.equal(toCodePoints(first, utf16.col), Math.min(span.col, Array.from(first).length), `${c.name}: back`);
    assert.equal(toCodePoints(last, utf16.end_col), Math.min(span.end_col, Array.from(last).length), `${c.name}: back to the end`);
  }
  assert.equal(toCodePoints("😀a", 1), 1, "half of a surrogate pair is one character");
  assert.equal(toCodePoints("ab", 9), 2, "clamped to the line");
  assert.equal(toCodeUnits("😀a", 9), 3, "clamped to the line");
});

test("a span's quote, prefix and suffix, case for case (the shared fixture)", () => {
  for (const c of FIXTURE.cases) {
    assert.deepEqual(contextOf(c.lines, c.span), { quote: c.quote, prefix: c.prefix, suffix: c.suffix }, c.name);
  }
});

test("a selection's span is the fixture's, from CodeMirror's offsets", () => {
  for (const c of FIXTURE.cases) {
    const state = stateOf(c.lines);
    const from = at(state, c.span.line, c.utf16.col), to = at(state, c.span.end, c.utf16.end_col);
    const got = spanOf(state, from, to, "h1");
    if (c.trimmed === null) {
      assert.equal(got, null, `${c.name}: a line break alone is no selection`);
      continue;
    }
    const stored = c.trimmed || c.span;
    assert.deepEqual(got, { position: { ...stored, prefix: c.prefix, suffix: c.suffix, hash: "h1" }, quote: c.quote }, c.name);
    assert.deepEqual(spanOf(state, to, from, "h1"), got, `${c.name}: either way round`);
  }
});

test("a selection ending at the start of a line is trimmed; an empty one and one on a cut line are none", () => {
  const lines = ["\\section{Intro}", "Weight decay is the oldest remedy", "", "and the cheapest."];
  const state = stateOf(lines);
  const got = spanOf(state, at(state, 2, 0), at(state, 4, 0), "h");
  assert.deepEqual([got.position.line, got.position.col, got.position.end, got.position.end_col], [2, 0, 2, 33]);
  assert.equal(got.quote, "Weight decay is the oldest remedy", "no trailing line break, past the empty line too");
  assert.equal(spanOf(state, 5, 5, "h"), null);
  assert.equal(spanOf(state, at(state, 2, 0), at(state, 2, 6), "h", new Set([2])), null, "a cut line's quote is not the file's");
  assert.equal(spanOf(state, at(state, 1, 0), at(state, 2, 6), "h", new Set([2, 9])), null, "touching it is enough");
  assert.ok(spanOf(state, at(state, 2, 0), at(state, 3, 0), "h", new Set([3])), "an end trimmed off a cut line does not touch it");
  assert.ok(spanOf(state, at(state, 4, 0), at(state, 4, 3), "h", new Set([2])));
  const long = stateOf(["x".repeat(TEXT_QUOTE_MAX + 50)]);
  const cut = spanOf(long, 0, TEXT_QUOTE_MAX + 50, "h");
  assert.equal(cut.quote.length, TEXT_QUOTE_MAX, "the quote is cut at its cap");
  assert.equal(cut.position.end_col, TEXT_QUOTE_MAX + 50, "the span is not");
  assert.equal(cut.position.suffix, "x".repeat(CONTEXT_CHARS), "the suffix follows the quote as stored");
});

test("a stored range's offsets: columns clamped per line, an end past the file at its end, a first line past it", () => {
  const state = stateOf(["x = \"😀😀\" + name", "short", "last line"]);
  const doc = state.doc;
  assert.deepEqual(rangeOf(doc, { line: 1, col: 11, end: 1, end_col: 15 }), { from: 13, to: 17, past: false }, "code points to units");
  assert.deepEqual(rangeOf(doc, { line: 2, col: 2, end: 2, end_col: 99 }), { from: at(state, 2, 2), to: at(state, 2, 5), past: false });
  assert.deepEqual(rangeOf(doc, { line: 2, col: 0, end: 9, end_col: 4 }), { from: at(state, 2, 0), to: doc.length, past: false });
  assert.deepEqual(rangeOf(doc, { line: 7, col: 0, end: 8, end_col: 4, lost: true }), { from: at(state, 3, 0), to: at(state, 3, 0), past: true });
  assert.equal(rangeOf(doc, { line: 0, col: 0, end: 1, end_col: 1 }), null, "malformed");
  assert.equal(rangeOf(doc, null), null);
});

test("the chat's lines: an end at a line's start belongs to the line before", () => {
  const state = stateOf(["one", "two", "", "four"]);
  assert.deepEqual(lineSpotOf(state.doc, at(state, 1, 1), at(state, 2, 2)), { line: 1, end: 2 });
  assert.deepEqual(lineSpotOf(state.doc, at(state, 2, 1), at(state, 2, 3)), { line: 2, end: 2 });
  assert.deepEqual(lineSpotOf(state.doc, at(state, 4, 0), at(state, 2, 0)), { line: 2, end: 2 }, "either way round, past an empty line");
  assert.equal(lineSpotOf(state.doc, 3, 3), null);
});

const hl = (position, extra = {}) => ({ id: "h1", quote: "Weight decay", color: "#ffe5648c", style: "highlight", note: "", hasNote: false,
  position: { line: 3, col: 0, end: 3, end_col: 12, prefix: "", suffix: "", hash: "f2", ...position }, ...extra });

test("what a highlight is drawn as: a mark with its flags as classes, a lost one a tag", () => {
  const plain = markSpec(hl({}), { fileHash: "f2" });
  assert.equal(plain.kind, "mark");
  assert.equal(plain.class, "docHl");
  assert.equal(plain.attributes["data-hl-id"], "h1");
  assert.match(plain.attributes.style, /--hl: #ffe5648c; --hl-dark: rgba\(.*\); --hl-dark-line: rgba\(.*\); --hl-mark: #[0-9a-f]{6}/);
  assert.equal(plain.attributes["data-hl-style"], undefined, "the wash is no line style");
  assert.equal(markSpec(hl({}, { style: "squiggly" })).attributes["data-hl-style"], "squiggly", "a range keeps a line style");
  assert.equal(markSpec(hl({ approx: true, match: 0.86 }), { fileHash: "f2" }).class, "docHl docHlApprox");
  assert.equal(markSpec(hl({ hash: "f1" }), { fileHash: "f2" }).class, "docHl docHlPending", "measured on another version, not placed yet");
  assert.equal(markSpec(hl({ hash: "f1", approx: true, match: 0.9 }), { fileHash: "f2" }).class, "docHl docHlApprox docHlPending",
    "placed to check on an older version is pending too, as the server reads it");
  assert.equal(markSpec(hl({ hash: "" }), { fileHash: "f2" }).class, "docHl", "an empty hash is the file's own");
  assert.equal(markSpec(hl({ hash: "f1" }), { fileHash: "" }).class, "docHl", "no file to tell it by");
  assert.equal(markSpec(hl({}, { threadCount: 2 }), { fileHash: "f2", active: true, flash: true }).class,
    "docHl docHlCommented docHlActive docHlFlash");
  assert.equal(markSpec(hl({}, { threadCount: 2, hasNote: true })).class, "docHl", "a note on it: no commented look");
  const link = markSpec(hl({}, { style: "underline", threadCount: 1, linkTarget: { url: "https://x", pageId: "", blockId: "" } }));
  assert.equal(link.class, "docHl docHlLink");
  assert.equal(link.attributes["data-hl-style"], undefined, "a link keeps its own look");
  const lost = markSpec(hl({ lost: true, approx: true }, { quote: "q".repeat(120), style: "underline" }), { active: true });
  assert.equal(lost.kind, "lost");
  assert.equal(lost.class, "docHlLost docHlActive");
  assert.equal(lost.attributes["data-hl-id"], "h1");
  assert.equal(lost.attributes.title, "q".repeat(80), "its quote's first 80 characters");
  assert.equal(lost.attributes["data-hl-style"], undefined);
});

test("every range's lines get the cue, lost and to check told apart", () => {
  assert.equal(lineCueSpec(hl({})).class, "docHlLine");
  assert.equal(lineCueSpec(hl({ lost: true, approx: true })).class, "docHlLine lost");
  assert.equal(lineCueSpec(hl({ approx: true, match: 0.9 })).class, "docHlLine approx");
  assert.match(lineCueSpec(hl({})).attributes.style, /^--hl-mark: #[0-9a-f]{6}; --hl-dark-line: \S.*$/,
    "its dark-tuned line tone too, which a dark theme takes");
});
