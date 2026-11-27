// The rules under the app's citation copies (src/citations/citeRules.js):
// the style in use where the one chosen is missing here, what a copy makes,
// a selection copied in the list's order, the pill's count of pages left
// out, the styles a menu offers in Zotero's order, the style a quote is
// cited in and its clipboard text.
import assert from "node:assert/strict";
import { test } from "node:test";
import { copyAsRows, copyPlan, inListOrder, quotePayload, quoteStyle, skippedNote, styleInUse, styleSections } from "../src/citations/citeRules.js";

test("an added style this server lacks is APA in use, once the account's list says so or a render found it gone", () => {
  const read = (ids, more = {}) => ({ loaded: true, styles: ids.map((id) => ({ id })), error: "", missing: [], ...more });
  const unread = { loaded: false, styles: [], error: "", missing: [] };
  assert.deepEqual(styleInUse({ wanted: "nature-physics", list: read(["acta-y"]) }), { style: "apa", gone: true });
  assert.deepEqual(styleInUse({ wanted: "nature-physics", list: read(["nature-physics"]) }), { style: "nature-physics", gone: false });
  assert.deepEqual(styleInUse({ wanted: "nature-physics", list: unread }), { style: "nature-physics", gone: false }, "not read yet");
  assert.equal(styleInUse({ wanted: "nature-physics", list: read([], { error: "offline" }) }).gone, false, "a failed read says nothing");
  assert.equal(styleInUse({ wanted: "nature-physics", list: { ...unread, missing: ["nature-physics"] } }).gone, true, "a render found it gone");
  assert.deepEqual(styleInUse({ wanted: "ieee", list: read([]) }), { style: "ieee", gone: false }, "a bundled style is always here");
});

test("a copy makes the Quick Copy format, a format asked by name, or a style's bibliography", () => {
  assert.deepEqual(copyPlan("citation", "pandoc", "ieee"), { format: "pandoc" });
  assert.deepEqual(copyPlan("bibliography", "ris", "ieee"), { format: "ris" });
  assert.deepEqual(copyPlan("citation", "style", "ieee"), { mode: "citation", style: "ieee" });
  assert.deepEqual(copyPlan("bibliography", "style", "ieee"), { mode: "bibliography", style: "ieee" });
  assert.deepEqual(copyPlan("style:nature", "pandoc", "ieee"), { mode: "bibliography", style: "nature", named: true });
  assert.deepEqual(copyPlan("biblatex", "style", "ieee"), { format: "biblatex" });
  // the share popover's boxes and the Reference line: the style's, whatever Quick Copy says
  assert.deepEqual(copyPlan({ mode: "citation" }, "pandoc", "ieee"), { mode: "citation", style: "ieee" });
});

test("a selection is copied in the order the list shows, not the order it was clicked in", () => {
  assert.deepEqual(inListOrder(["c", "a", "c"], ["a", "b", "c"]), ["a", "c"]);
  // a page the list does not show (picked from the Files window) comes after
  assert.deepEqual(inListOrder(["x", "c", "a"], ["a", "b", "c"]), ["a", "c", "x"]);
});

test("the pill counts the pages left out, and says so when none could be cited", () => {
  assert.equal(skippedNote(5, 0), "");
  assert.equal(skippedNote(5, 2), "2 of 5 pages have no paper metadata.");
  assert.equal(skippedNote(5, 1), "1 of 5 pages has no paper metadata.");
  assert.equal(skippedNote(3, 3), "None of these pages has paper metadata to cite.");
  assert.equal(skippedNote(1, 1), "This page has no paper metadata to cite.");
});

test("Copy as ▸ lists the default and the added styles, then the other bundled ones", () => {
  const added = [{ id: "zeitschrift-x", title: "Zeitschrift X", kind: "note" }, { id: "acta-y", title: "Acta Y", kind: "numeric" }];
  const { mine, others } = copyAsRows({ style: "ieee", added });
  assert.deepEqual(mine.map((s) => s.id), ["ieee", "acta-y", "zeitschrift-x"]);
  assert.ok(!others.some((s) => s.id === "ieee") && others.some((s) => s.id === "apa"));
  assert.equal(others.length, 15);
  // an added default leads, and no bundled style is listed twice
  const own = copyAsRows({ style: "zeitschrift-x", added });
  assert.deepEqual(own.mine.map((s) => s.id), ["zeitschrift-x", "acta-y"]);
  assert.equal(own.others.length, 16);
});

test("the style picker's sections follow the classes, an added style in its own, an unknown default under Other", () => {
  const sections = styleSections({ added: [{ id: "zeitschrift-x", title: "Zeitschrift X", kind: "note" }], current: "gone-elsewhere" });
  assert.deepEqual(sections.map((s) => s.kind), ["author-date", "author", "numeric", "note", ""]);
  assert.ok(sections.find((s) => s.kind === "note").options.some(([id]) => id === "zeitschrift-x"));
  assert.deepEqual(sections.at(-1).options, [["gone-elsewhere", "gone-elsewhere"]]);
  const titles = sections[0].options.map(([, title]) => title);
  assert.deepEqual(titles, [...titles].sort((a, b) => a.localeCompare(b)));
});

test("a quote is cited by name: the default when it cites so, else APA", () => {
  assert.equal(quoteStyle("chicago-author-date", "author-date"), "chicago-author-date");
  assert.equal(quoteStyle("modern-language-association", "author"), "modern-language-association");
  assert.equal(quoteStyle("ieee", "numeric"), "apa");
  assert.equal(quoteStyle("chicago-notes-bibliography", "note"), "apa");
});

test("a quote goes on one line in curly quotes, escaped in its HTML, its citation after it", () => {
  assert.deepEqual(quotePayload("a <b> &\n  c", { text: "(Smith, 2020, p. 3)", html: "(Smith, 2020, p. 3)" }),
    { text: "“a <b> & c” (Smith, 2020, p. 3)", html: "“a &lt;b&gt; &amp; c” (Smith, 2020, p. 3)" });
  assert.deepEqual(quotePayload("alone"), { text: "“alone”", html: "“alone”" });
});
