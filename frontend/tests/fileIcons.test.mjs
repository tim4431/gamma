// A synced folder's file and directory icons (shared/ui/fileIcons.js): the
// kind a name says — a whole name over its stem, a stem over an extension,
// the longest dotted extension, a directory's name without its dots and
// underscores — and that every kind the tables give has its drawing.
import assert from "node:assert/strict";
import { test } from "node:test";
import { EXTENSIONS, FILE_NAMES, FILE_STEMS, FOLDER_NAMES, KIND_MARKS, fileKind, folderKind, glyphKind } from "../src/shared/ui/fileIcons.js";

test("a file by its whole name, its stem, a dotfile's form, then its longest extension", () => {
  const cases = {
    "paper/main.tex": "tex", "refs.bib": "references", "README.md": "readme", README: "readme", "LICENSE.txt": "license",
    "notes.MD": "doc", "analysis.ipynb": "notebook", "run.h5": "data", "data.csv": "table",
    "x/package-lock.json": "lock", "package.json": "json", Makefile: "config", "requirements.txt": "config",
    ".gitignore": "git", ".env": "config", ".env.local": "config", ".latexmkrc": "config",
    "build.tar.gz": "archive", "main.synctex.gz": "plain", "src/index.d.ts": "code", "fig1.PDF": "pdf",
    "notes.zzz": "", noextension: "", "": "",
  };
  for (const [path, kind] of Object.entries(cases)) assert.equal(fileKind(path), kind, path);
});

test("a directory by its name, without the dots and underscores around it", () => {
  const cases = {
    legacy: "archive", notes: "doc", paper: "tex", references: "references", results: "results", simulations: "test",
    tmp: "temp", "repo/.github": "git", __pycache__: "temp", ".ipynb_checkpoints": "temp", Figures: "image",
    node_modules: "packages", "paper/sections": "tex", misc: "", "": "",
  };
  for (const [path, kind] of Object.entries(cases)) assert.equal(folderKind(path), kind, path);
});

test("a page's format draws a name of no kind", () => {
  assert.equal(glyphKind("tex", "text"), "tex");
  assert.equal(glyphKind("", "text"), "code");
  assert.equal(glyphKind("", "binary"), "plain");
  assert.equal(glyphKind("", "pdf"), "pdf");
  assert.equal(glyphKind("", ""), "doc");
  assert.equal(glyphKind("nonsense", "markdown"), "doc");
});

test("every name has one kind, and every kind its mark", () => {
  for (const table of [FILE_NAMES, FILE_STEMS, EXTENSIONS, FOLDER_NAMES]) {
    const seen = new Map();
    for (const [kind, keys] of Object.entries(table)) {
      assert.ok(Object.hasOwn(KIND_MARKS, kind), `${kind} has a mark`);
      for (const key of keys) {
        assert.equal(key, key.toLowerCase(), `${key} is lowercased`);
        assert.ok(!seen.has(key), `${key} is both ${seen.get(key)} and ${kind}`);
        seen.set(key, kind);
      }
    }
  }
});
