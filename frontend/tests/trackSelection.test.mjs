// The tracking dialog's choice (explorer/trackSelection.js): a base and the
// changes made on it, in order, as the server evaluates them; each row's
// box, a folder's over what is loaded below it.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  FORMATS, body, boxOf, initial, isSelected, ofFormat, tickChange, toggle, toggleAll, toggleFormat,
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

test("a file opened with Gamma is ticked as the dialog opens, unless something ticks it already", () => {
  const start = initial("preset");
  const step = tickChange(start, byPath, "main.tex");
  assert.equal(step.why, "");
  assert.equal(step.read, "");
  assert.equal(step.entry, at("main.tex"));
  assert.deepEqual(step.state.changes, [{ path: "main.tex", on: true }]);
  assert.equal(isSelected(step.state, at("main.tex")), true);
  assert.deepEqual(tickChange(start, byPath, "figures/plot.py").state.changes, [{ path: "figures/plot.py", on: true }], "in a folder read");
  assert.equal(tickChange(start, entries, "main.tex").state.changes.length, 1, "the entries as a list too");
  // ticked already: by its base, by a change, or always (locked)
  assert.equal(tickChange(start, byPath, "notes.md").state, start, "the preset ticks it");
  const unticked = toggle(start, at("notes.md"), false);
  assert.deepEqual(tickChange(unticked, byPath, "notes.md").state.changes,
    [{ path: "notes.md", on: true }], "a change of its own that unticked it gives way");
  const latex = toggleFormat(start, "latex", true);
  assert.equal(tickChange(latex, byPath, "refs.bib").state, latex, "a format change ticks it");
  assert.equal(tickChange(start, byPath, "vendor.pdf").state, start, "a locked file");
  assert.equal(tickChange(start, byPath, "vendor.pdf").why, "");
});

test("a file opened with Gamma that is never tracked, or gone, is not ticked", () => {
  const start = initial("file");
  const dot = new Map([...byPath, [".env", { path: ".env", directory: false, format: "text", selected: false, never: "dot" }]]);
  const never = tickChange(start, dot, ".env");
  assert.equal(never.why, "never");
  assert.equal(never.entry.never, "dot", "its row's code says why");
  assert.equal(never.state, start);
  const inside = tickChange(start, byPath, ".git/config");
  assert.deepEqual([inside.why, inside.entry?.path, inside.read], ["never", ".git", ""], "a folder on its way never tracked, not read");
  assert.equal(tickChange(start, byPath, "gone.tex").why, "gone");
  assert.equal(tickChange(start, byPath, "nowhere/a.tex").why, "gone", "a folder the listing does not have");
  assert.equal(tickChange(start, byPath, "figures").why, "gone", "a folder is no file");
  assert.equal(tickChange(start, byPath, "gone.tex").state, start);
});

test("the folders down to a file opened with Gamma are read first, one at a time", () => {
  const start = initial("preset");
  const first = tickChange(start, byPath, "build/out/main.log");
  assert.deepEqual([first.read, first.why, first.entry], ["build", "", null], "build/ is listed, not read");
  assert.equal(first.state, start, "nothing ticked yet");
  const read = new Map([...byPath, ["build", { ...at("build"), walked: true }],
    ["build/main.aux", { path: "build/main.aux", directory: false, format: "binary", selected: false }],
    ["build/out", { path: "build/out", directory: true, walked: false, selected: false, git_ignored: true }]]);
  assert.equal(tickChange(start, read, "build/out/main.log").read, "build/out", "then the folder below it");
  const done = new Map([...read, ["build/out", { ...read.get("build/out"), walked: true }],
    ["build/out/main.log", { path: "build/out/main.log", directory: false, format: "text", selected: false }]]);
  const step = tickChange(start, done, "build/out/main.log");
  assert.deepEqual([step.read, step.why], ["", ""]);
  assert.deepEqual(step.state.changes, [{ path: "build/out/main.log", on: true }]);
  assert.equal(tickChange(start, read, "build/gone.tex").why, "gone", "a read folder without it");
});
