// The tracking dialog's choice (explorer/trackSelection.js): a base and the
// changes made on it, in order, as the server evaluates them; each row's
// box, a folder's over what is loaded below it.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  FORMATS, body, boxOf, initial, isSelected, ofFormat, toggle, toggleAll, toggleFormat,
} from "../src/explorer/trackSelection.js";

// A paper's directory as the tree route lists it under the preset: build/
// is git-ignored, so not walked; .git is never tracked.
const entries = [
  { path: "main.tex", directory: false, format: "text", language: "latex", selected: false },
  { path: "refs.bib", directory: false, format: "text", language: "bibtex", selected: false },
  { path: "main.pdf", directory: false, format: "pdf", language: null, selected: true, git_ignored: true },
  { path: "notes.md", directory: false, format: "markdown", language: null, selected: true },
  { path: "figures", directory: true, walked: true, selected: true },
  { path: "figures/fig1.pdf", directory: false, format: "pdf", language: null, selected: true },
  { path: "figures/fig2.pdf", directory: false, format: "pdf", language: null, selected: true },
  { path: "figures/plot.py", directory: false, format: "text", language: "python", selected: false },
  { path: "figures/old", directory: true, walked: true, selected: true },
  { path: "build", directory: true, walked: false, selected: false, git_ignored: true },
  { path: ".git", directory: true, walked: false, never: "dot" },
  { path: "vendor.pdf", directory: false, format: "pdf", language: null, selected: true, locked: "followed" },
];
const byPath = new Map(entries.map((e) => [e.path, e]));
const at = (path) => byPath.get(path);
const chosen = (state) => entries.filter((e) => !e.directory && isSelected(state, e)).map((e) => e.path);
const box = (state, path) => boxOf(state, path === "" ? { path: "", directory: true } : at(path), byPath);

test("the base, then each change in order", () => {
  const start = initial("preset");
  assert.deepEqual(start, { base: "preset", changes: [] });
  assert.deepEqual(chosen(start), ["main.pdf", "notes.md", "figures/fig1.pdf", "figures/fig2.pdf", "vendor.pdf"]);
  const s = toggle(toggleFormat(start, "latex", true), at("figures/fig2.pdf"), false);
  assert.deepEqual(chosen(s), ["main.tex", "refs.bib", "main.pdf", "notes.md", "figures/fig1.pdf", "vendor.pdf"]);
  assert.deepEqual(body(s), { base: "preset", changes: [{ format: "latex", on: true }, { path: "figures/fig2.pdf", on: false }] });
  assert.notEqual(body(s).changes[0], s.changes[0], "the request's own copy");
});

test("a folder ticked or unticked takes the place of the changes below it; a format's apply wherever", () => {
  let s = toggle(initial("preset"), at("figures/fig1.pdf"), false);
  s = toggle(s, at("figures/plot.py"), true);
  s = toggleFormat(s, "pdf", false);
  s = toggle(s, at("figures"), false);
  assert.deepEqual(s.changes, [{ format: "pdf", on: false }, { path: "figures/", on: false }], "the file changes below it go");
  assert.deepEqual(chosen(s), ["notes.md", "vendor.pdf"]);
  s = toggleFormat(s, "pdf", true);
  assert.deepEqual(s.changes, [{ path: "figures/", on: false }, { format: "pdf", on: true }], "the earlier PDFs change goes");
  assert.deepEqual(chosen(s), ["main.pdf", "notes.md", "figures/fig1.pdf", "figures/fig2.pdf", "vendor.pdf"],
    "the later format change ticks the PDFs inside the unticked folder");
  s = toggle(s, at("figures/fig1.pdf"), false);
  s = toggle(s, at("figures/fig1.pdf"), true);
  assert.deepEqual(s.changes.at(-1), { path: "figures/fig1.pdf", on: true });
  assert.equal(s.changes.filter((c) => c.path === "figures/fig1.pdf").length, 1, "a file's earlier change of itself goes");
  assert.deepEqual(toggle(s, { path: "figures/", directory: true }, true).changes,
    [{ format: "pdf", on: true }, { path: "figures/", on: true }], "a folder's path with its \"/\" is the same folder");
});

test("a row never tracked, or always, stays as it is; so does the top, which is toggleAll's", () => {
  const s = initial("file");
  assert.equal(toggle(s, at(".git"), true), s);
  assert.equal(toggle(s, at("vendor.pdf"), false), s);
  assert.equal(toggle(s, { path: "", directory: true }, true), s);
  assert.equal(isSelected(s, { ...at("main.pdf"), never: "name" }), false);
  assert.equal(isSelected(toggleFormat(s, "pdf", false), at("vendor.pdf")), true, "a locked file is always taken");
  assert.deepEqual(box(s, ".git"), { checked: false, indeterminate: false, disabled: true });
  assert.deepEqual(box(s, "vendor.pdf"), { checked: true, indeterminate: false, disabled: true });
});

test("Select all and Deselect all: one change for each entry at the top", () => {
  const s = toggleAll(toggle(initial("preset"), at("figures/fig2.pdf"), false), entries, false);
  assert.deepEqual(s.changes.map((c) => c.path), ["main.tex", "refs.bib", "main.pdf", "notes.md", "figures/", "build/"]);
  assert.deepEqual(chosen(s), ["vendor.pdf"]);
  assert.deepEqual(chosen(toggleAll(s, entries, true)), entries.filter((e) => !e.directory).map((e) => e.path));
  assert.equal(box(toggleAll(s, entries, true), "").checked, true);
});

test("a folder's box: ticked, part ticked or not, over what is loaded below it", () => {
  let s = initial("preset");
  assert.deepEqual(box(s, "figures"), { checked: false, indeterminate: true, disabled: false }, "plot.py is not");
  s = toggle(s, at("figures/plot.py"), true);
  assert.deepEqual(box(s, "figures"), { checked: true, indeterminate: false, disabled: false });
  s = toggle(s, at("figures"), false);
  assert.deepEqual(box(s, "figures"), { checked: false, indeterminate: false, disabled: false });
  assert.equal(box(s, "").indeterminate, true, "the whole directory");
  assert.deepEqual(box(s, "figures/old"), { checked: false, indeterminate: false, disabled: false },
    "an empty folder by its own change");
  assert.equal(box(initial("preset"), "figures/old").checked, true, "or its base");
});

test("an unwalked folder goes by its own change or its base; a format change or one below it makes it part ticked", () => {
  let s = initial("preset");
  assert.deepEqual(box(s, "build"), { checked: false, indeterminate: false, disabled: false }, "git ignores it: off");
  s = toggleFormat(s, "pdf", true);
  assert.equal(box(s, "build").indeterminate, true, "a PDF inside it may be ticked now");
  s = toggle(s, at("build"), true);
  assert.deepEqual(box(s, "build"), { checked: true, indeterminate: false, disabled: false }, "its own change decides");
  s = toggle(s, { path: "build/main.aux", directory: false }, false);
  assert.equal(box(s, "build").indeterminate, true, "a file below it unticked");
  assert.equal(box(toggle(initial("preset"), { path: "build", directory: true }, true), "build").checked, true);
  // Once read (the dialog opened it), its files count, whatever its row says.
  const read = new Map([...byPath, ["build/main.aux", { path: "build/main.aux", directory: false, format: "binary", selected: false }]]);
  assert.deepEqual(boxOf(initial("preset"), at("build"), read), { checked: false, indeterminate: false, disabled: false });
  assert.equal(boxOf(toggle(initial("preset"), at("build"), true), at("build"), read).checked, true);
});

test("an unwalked folder its base tracks is part ticked until a change covers it whole", () => {
  const big = { path: "big", directory: true, walked: false, selected: true };
  const only = new Map([["big", big]]);
  assert.deepEqual(boxOf(initial("file"), big, only), { checked: false, indeterminate: true, disabled: false },
    "the base may leave out files nobody has read");
  assert.equal(boxOf(toggle(initial("file"), big, true), big, only).checked, true);
  assert.deepEqual(boxOf(toggle(initial("file"), big, false), big, only), { checked: false, indeterminate: false, disabled: false });
  assert.equal(boxOf(initial("file"), { ...big, walked: true }, new Map()).checked, true, "an empty folder by its base");
});

test("the format filters: PDFs, notes, LaTeX and BibTeX sources, pictures", () => {
  assert.deepEqual(FORMATS, ["pdf", "markdown", "latex", "image"]);
  assert.equal(ofFormat(at("main.tex"), "latex"), true);
  assert.equal(ofFormat(at("refs.bib"), "latex"), true);
  assert.equal(ofFormat(at("figures/plot.py"), "latex"), false);
  assert.equal(ofFormat(at("notes.md"), "markdown"), true);
  assert.equal(ofFormat(at("figures"), "pdf"), false, "a folder is of no format");
  assert.equal(ofFormat({ path: "a.png", format: "image" }, "image"), true);
});
