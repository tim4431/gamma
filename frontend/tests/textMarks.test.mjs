// The code view's highlights as decorations (src/documents/viewers/
// textMarks.js): a placed range's mark, clamped to its lines; a lost one's
// tag at its old first line, at the last line when that is past the end,
// and no mark over the text there now; the line cue on every line of
// every range; the classes markSpec gives; the field rebuilt when the text
// changes; the range a press lands on; and the popup's and the column's
// pure parts (inCode, hasRoom, standingLine).
import test from "node:test";
import assert from "node:assert/strict";
import { EditorState } from "@codemirror/state";
import {
  MARGIN_W, MIN_TEXT_W, hasRoom, highlightAt, highlightDecorations, highlightField, inCode, setHighlights, standingLine,
} from "../src/documents/viewers/textMarks.js";
import { CARD_W, COLUMN_GAP, EDGE } from "../src/comments/marginLayout.js";

const TEXT = ["\\section{Intro}", "Weight decay is the oldest remedy.", "", "It helps.", "\\end{document}"].join("\n");
const doc = EditorState.create({ doc: TEXT }).doc;
const pos = (line, col, end, end_col, extra = {}) => ({ line, col, end, end_col, prefix: "", suffix: "", hash: "v1", ...extra });
const hl = (id, position, extra = {}) => ({ id, color: "#ffeb3b", quote: "q", position, ...extra });

// Every decoration of a set: {from, to, kind: "mark" | "widget" | "line", class, id}.
function list(set) {
  const out = [];
  set.between(0, doc.length + 1, (from, to, value) => {
    const spec = value.spec;
    const kind = spec.widget ? "widget" : from === to && !spec.widget ? "line" : "mark";
    const s = spec.widget ? spec.widget.spec : spec;
    out.push({ from, to, kind, class: s.class || "", id: s.attributes?.["data-hl-id"] || null });
  });
  return out;
}
const at = (n) => doc.line(n).from;

test("a placed range is a mark over its text, its columns clamped to their lines, a cue on each line", () => {
  const got = list(highlightDecorations(doc, { highlights: [hl("a", pos(2, 0, 2, 12)), hl("b", pos(2, 20, 4, 99))], fileHash: "v1" }));
  const marks = got.filter((d) => d.kind === "mark");
  assert.deepEqual(marks.map((d) => [d.id, d.from, d.to]), [["a", at(2), at(2) + 12], ["b", at(2) + 20, at(4) + "It helps.".length]]);
  assert.ok(marks.every((d) => d.class.split(" ").includes("docHl")));
  const cues = got.filter((d) => d.kind === "line");
  assert.deepEqual(cues.map((d) => d.from), [at(2), at(2), at(3), at(4)], "one cue per line of each range, the empty line too");
  assert.ok(cues.every((d) => d.class === "docHlLine"));
});

test("a lost range is a tag at its old first line, drawing nothing over the text there now", () => {
  const got = list(highlightDecorations(doc, { highlights: [hl("l", pos(4, 3, 5, 4, { lost: true }))] }));
  assert.equal(got.filter((d) => d.kind === "mark").length, 0);
  const tags = got.filter((d) => d.kind === "widget");
  assert.deepEqual(tags.map((d) => [d.id, d.from]), [["l", at(4)]]);
  assert.match(tags[0].class, /\bdocHlLost\b/);
  assert.deepEqual(got.filter((d) => d.kind === "line").map((d) => [d.from, d.class]), [[at(4), "docHlLine lost"], [at(5), "docHlLine lost"]]);
});

test("a lost range past the file's end stands at its last line; two on one line are two tags", () => {
  const got = list(highlightDecorations(doc, { highlights: [hl("p", pos(40, 0, 42, 5, { lost: true })), hl("q", pos(5, 0, 5, 3, { lost: true }))] }));
  assert.deepEqual(got.filter((d) => d.kind === "widget").map((d) => [d.id, d.from]).sort(), [["p", at(5)], ["q", at(5)]]);
  assert.deepEqual(got.filter((d) => d.kind === "line").map((d) => d.from), [at(5), at(5)]);
});

test("a placed range past the end (measured on another version) and a malformed one draw nothing", () => {
  const got = list(highlightDecorations(doc, { highlights: [hl("x", pos(9, 0, 9, 3, { hash: "v0" })), hl("y", { line: 2, col: 5, end: 1, end_col: 0 })], fileHash: "v1" }));
  assert.deepEqual(got, []);
});

test("the classes: to check, pending on another version, commented, a link, the active card's and the flash", () => {
  const classOf = (h, opts) => list(highlightDecorations(doc, { highlights: [h], ...opts })).find((d) => d.kind === "mark").class.split(" ");
  assert.ok(classOf(hl("a", pos(2, 0, 2, 6, { approx: true, match: 0.8 })), { fileHash: "v1" }).includes("docHlApprox"));
  assert.ok(classOf(hl("a", pos(2, 0, 2, 6, { hash: "v0" })), { fileHash: "v1" }).includes("docHlPending"));
  assert.ok(!classOf(hl("a", pos(2, 0, 2, 6)), { fileHash: "v1" }).includes("docHlPending"));
  assert.ok(classOf(hl("a", pos(2, 0, 2, 6), { threadCount: 2 }), {}).includes("docHlCommented"));
  assert.ok(classOf(hl("a", pos(2, 0, 2, 6), { linkTarget: { url: "https://x" } }), {}).includes("docHlLink"));
  const both = classOf(hl("a", pos(2, 0, 2, 6)), { active: "a", flash: "a" });
  assert.ok(both.includes("docHlActive") && both.includes("docHlFlash"));
  assert.ok(!classOf(hl("a", pos(2, 0, 2, 6)), { active: "b", flash: null }).includes("docHlActive"));
  const approxCue = list(highlightDecorations(doc, { highlights: [hl("a", pos(2, 0, 2, 6, { approx: true, match: 0.8 }))] }))
    .find((d) => d.kind === "line");
  assert.equal(approxCue.class, "docHlLine approx");
});

test("the field draws what setHighlights says, again on the new text when the text changes", () => {
  let state = EditorState.create({ doc: TEXT, extensions: [highlightField] });
  assert.equal(state.field(highlightField).deco.size, 0);
  state = state.update({ effects: setHighlights.of({ highlights: [hl("a", pos(4, 0, 4, 2))], fileHash: "v1" }) }).state;
  const markAt = (s) => {
    let found = null;
    s.field(highlightField).deco.between(0, s.doc.length, (from, to, v) => { if (!v.spec.widget && from < to) found = [from, to]; });
    return found;
  };
  assert.deepEqual(markAt(state), [state.doc.line(4).from, state.doc.line(4).from + 2]);
  // a new version of the file, read with three lines more above: the range
  // stays on the line its position names until the server places it again
  state = state.update({ changes: { from: 0, to: state.doc.length, insert: `% a\n% b\n% c\n${TEXT}` } }).state;
  assert.deepEqual(markAt(state), [state.doc.line(4).from, state.doc.line(4).from + 2]);
  state = state.update({ effects: setHighlights.of(null) }).state;
  assert.equal(state.field(highlightField).deco.size, 0);
});

test("a press finds the innermost range at its offset", () => {
  const state = EditorState.create({ doc: TEXT, extensions: [highlightField] }).update({
    effects: setHighlights.of({ highlights: [hl("outer", pos(2, 0, 2, 30)), hl("inner", pos(2, 7, 2, 12)), hl("gone", pos(2, 0, 2, 3, { lost: true }))] }),
  }).state;
  assert.equal(highlightAt(state, at(2) + 9), "inner");
  assert.equal(highlightAt(state, at(2) + 20), "outer");
  assert.equal(highlightAt(state, at(4) + 1), null);
});

test("a press next to a range is not on it: only the character on the press's side counts", () => {
  // "def" in "abc def" (4..7): a range that ends at its line's end, as one made from a line's selection does
  const state = EditorState.create({ doc: "abc def\nghi", extensions: [highlightField] }).update({
    effects: setHighlights.of({ highlights: [hl("h1", pos(1, 4, 1, 7))] }),
  }).state;
  assert.equal(highlightAt(state, 4), "h1", "the left half of its first character");
  assert.equal(highlightAt(state, 4, -1), null, "the right half of the character before it");
  assert.equal(highlightAt(state, 7, -1), "h1", "the right half of its last character");
  assert.equal(highlightAt(state, 7), null, "past its end: the blank right of the line");
  assert.equal(highlightAt(state, 5), "h1");
  assert.equal(highlightAt(state, 3), null);
});

// A stand-in DOM node: an element with a class list and a parent, or a
// text node under one.
function el(cls, parent = null) {
  const node = { nodeType: 1, cls, parentElement: parent };
  node.closest = (sel) => {
    const want = sel.split(",").map((s) => s.trim().slice(1));
    for (let n = node; n; n = n.parentElement) if (want.includes(n.cls)) return n;
    return null;
  };
  return node;
}
const textIn = (parent) => ({ nodeType: 3, parentElement: parent });
function selection(start, end, { collapsed = false } = {}) {
  return { isCollapsed: collapsed, rangeCount: 1, getRangeAt: () => ({ startContainer: start, endContainer: end }) };
}

test("inCode: a selection with both ends in this view's content, not in its panels", () => {
  const root = el("cm-editor");
  const other = el("cm-editor");
  root.contains = (n) => { for (let x = n; x; x = x.parentElement) if (x === root) return true; return false; };
  const content = el("cm-content", el("cm-scroller", root));
  const line = el("cm-line", content);
  const panel = el("cm-search", el("cm-panels", root));
  const elsewhere = el("cm-line", el("cm-content", other));
  assert.equal(inCode(selection(textIn(line), textIn(line)), root), true);
  assert.equal(inCode(selection(textIn(line), content), root), true, "an end on the content itself");
  assert.equal(inCode(selection(textIn(line), textIn(line), { collapsed: true }), root), false);
  assert.equal(inCode(selection(textIn(panel), textIn(panel)), root), false, "Find's field");
  assert.equal(inCode(selection(textIn(line), textIn(elsewhere)), root), false, "an end in another view");
  assert.equal(inCode(null, root), false);
});

test("the column stands with room for MIN_TEXT_W of text beside it, at a range's first line", () => {
  assert.equal(MARGIN_W, CARD_W + COLUMN_GAP + EDGE);
  const least = 48 + MIN_TEXT_W + COLUMN_GAP + CARD_W + 2 * EDGE;
  assert.equal(hasRoom(least, 48), true);
  assert.equal(hasRoom(least - 2, 48), false);
  assert.equal(hasRoom(0, 0), false);
  assert.equal(standingLine(pos(12, 0, 14, 3), 100), 12);
  assert.equal(standingLine(pos(120, 0, 121, 3, { lost: true }), 100), 100, "past the end: the last line");
  assert.equal(standingLine(pos(120, 0, 121, 3), 100), 0, "placed past the lines shown: no card beside other text");
  assert.equal(standingLine({ line: 0 }, 100), 0);
});
