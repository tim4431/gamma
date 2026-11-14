// A synced folder's file icons (shared/ui/fileIcons.js iconFor) against the
// theme's own manifest, as frontend/tools/fileIcons.mjs compacts it: the
// cases VS Code was seen to draw with Material Icon Theme (a whole name over
// an extension, the longest extension, a key under its parent only, the
// built-in languages, folders, the root, the light variants), and the open
// folders' ids the map derives instead of carrying.
import assert from "node:assert/strict";
import { test } from "node:test";
import { iconFor, languageOf } from "../src/shared/ui/fileIcons.js";
import { compactMap, iconFiles, themeManifest } from "../tools/fileIcons.mjs";

const manifest = themeManifest();
const map = compactMap(manifest);

test("a file by its whole name, then its longest extension, then its language", () => {
  const cases = {
    "src/index.d.ts": "typescript-def", "src/index.ts": "typescript", "app.test.js": "test-js", "app.spec.ts": "test-ts", "a.min.js": "javascript",
    Dockerfile: "docker", "readme.MD": "readme", "main.tex": "tex", "analysis.py": "python", "tsconfig.app.json": "tsconfig",
    ".config/graphqlrc": "graphql", "x/graphqlrc": "file", "notes.zzz": "file",
  };
  for (const [path, icon] of Object.entries(cases)) assert.equal(iconFor(map, path), icon, path);
});

test("the built-in languages the theme reaches only by a language id", () => {
  assert.equal(languageOf("docker/dockerfile.dev"), "dockerfile");
  assert.equal(languageOf("repo/.git/config"), "properties");
  assert.equal(languageOf("x/.claude/rules/a/b.md"), "instructions");
  assert.equal(languageOf("tsconfig.base.json"), "jsonc");
  assert.equal(languageOf("main.tex"), "");
  assert.equal(iconFor(map, "docker/Dockerfile.dev"), map.languageIds.dockerfile);
  assert.equal(iconFor(map, "Jenkinsfile.ci"), map.languageIds.groovy);
  assert.equal(iconFor(map, ".git/config"), map.languageIds.properties);
});

test("folders by name, under their parent only, and the root always its own", () => {
  assert.equal(iconFor(map, ".github/workflows", { dir: true }), "folder-gh-workflows");
  assert.equal(iconFor(map, "x/workflows", { dir: true }), "folder");
  assert.equal(iconFor(map, "Docs", { dir: true }), "folder-docs");
  assert.equal(iconFor(map, "paper/figures", { dir: true }), manifest.folderNames.figures);
  assert.equal(iconFor(map, "paper/figures", { dir: true, open: true }), manifest.folderNamesExpanded.figures);
  assert.equal(iconFor(map, "zzz", { dir: true, open: true }), "folder-open");
  assert.equal(iconFor(map, "Docs", { root: true }), "folder-root");
  assert.equal(iconFor(map, "Docs", { root: true, open: true }), "folder-root-open");
});

test("the light theme's variants where it has them", () => {
  assert.equal(iconFor(map, "foo.jinja", { light: true }), "jinja_light");
  assert.equal(iconFor(map, "foo.jinja"), "jinja");
  assert.equal(iconFor(map, "jinja", { dir: true, open: true, light: true }), "folder-jinja-open_light");
  assert.equal(iconFor(map, "main.tex", { light: true }), "tex");
});

test("an open folder's derived id is the manifest's, and every id has its SVG", () => {
  for (const [key, id] of Object.entries(manifest.folderNamesExpanded)) assert.equal(iconFor(map, key, { dir: true, open: true }), id, key);
  for (const [key, id] of Object.entries(manifest.light.folderNamesExpanded)) {
    assert.equal(iconFor(map, key, { dir: true, open: true, light: true }), id, key);
  }
  const ids = new Set(iconFiles(manifest).map(([id]) => id));
  for (const table of [map.fileExtensions, map.fileNames, map.folderNames, map.languageIds, map.light.fileNames, map.light.folderNames]) {
    for (const id of Object.values(table)) assert.ok(ids.has(id), id);
  }
  assert.ok(ids.has("folder-open") && ids.has("folder-root-open"));
});
