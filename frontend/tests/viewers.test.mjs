// node --test tests/viewers.test.mjs (from frontend/) — the viewer registry
// (documents/viewers.js): every format but markdown has a pane, every pane
// a descriptor whose strip tools are the strip's items; and the code
// language map (documents/codeLanguages.js), each of the registry's text
// languages mapped to a description @codemirror/language-data has, or to
// plain text.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { LanguageDescription } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { CODE_LANGUAGE, codeLanguageName } from "../src/documents/codeLanguages.js";
import { EXTENSIONS, FORMATS, NAMES } from "../src/documents/formats.js";
import { pageDocument } from "../src/documents/pageDocument.js";
import { FORMAT_VIEWER, VIEWERS, viewerOf } from "../src/documents/viewers.js";
import { VIEWER_TOOL_ITEMS } from "../src/app/viewerToolItems.js";

const DOCUMENT_CASES = JSON.parse(readFileSync(new URL("../../tests/shared/document.json", import.meta.url), "utf8"));

const PLAIN = new Set(["plaintext", "csv", "tsv", "bibtex", "typst", "rst", "makefile"]);

test("every format but markdown has a viewer, and every viewer a descriptor the frame reads", () => {
  for (const format of FORMATS) {
    assert.equal(viewerOf(format) !== null, format !== "markdown", format);
    if (format !== "markdown") assert.ok(VIEWERS[viewerOf(format)], format);
  }
  const ids = new Set([...Object.values(FORMAT_VIEWER), "notebook"]);
  assert.deepEqual(new Set(Object.keys(VIEWERS)), ids, "a descriptor per pane, the notebook's included, and no other");
  const tools = new Set(VIEWER_TOOL_ITEMS.map((item) => item.id));
  for (const [id, spec] of Object.entries(VIEWERS)) {
    assert.ok(Array.isArray(spec.tools) && spec.tools.every((tool) => tools.has(tool)), `${id}: strip items`);
    assert.ok(spec.word && spec.close, `${id}: its word and its close title`);
    assert.equal(!!spec.ink, id === "pdf" || id === "notebook", `${id}: the markup strip hangs on the PDF and the notebook`);
    assert.equal(!!spec.host, id === "pdf" || id === "notebook", `${id}: the host renders the PDF and the notebook`);
    assert.equal(!!spec.regions, id === "image", `${id}: regions are the picture's`);
    assert.equal(!!spec.margin, id === "pdf" || id === "image" || id === "text",
      `${id}: the comment margin stands beside the PDF, the picture and the code view`);
  }
  assert.ok(VIEWERS.image.tools.includes("zoom") && VIEWERS.image.tools.includes("selectMode"));
  assert.deepEqual(VIEWERS.text.tools, ["highlighter"], "the code view takes the text highlighters");
  assert.equal(VIEWERS.pdf.word, "PDF", "the format token stays untranslated");
});

test("every text language of the registry maps to a language-data description, or to plain text", () => {
  const found = (name) => LanguageDescription.matchLanguageName(languages, name, false);
  const registry = new Set([...Object.values(EXTENSIONS).filter((row) => row.format === "text").map((row) => row.language),
    ...Object.values(NAMES).filter(([format]) => format === "text").map(([, language]) => language),
    "markdown"]); // languageOf's word for a Markdown file read as text (an opened .md)
  for (const language of registry) {
    const name = Object.hasOwn(CODE_LANGUAGE, language) ? CODE_LANGUAGE[language] : null;
    if (PLAIN.has(language)) assert.equal(name, null, `${language} is shown plain`);
    else assert.ok(name && found(name), `${language} → ${name}`);
  }
  for (const language of Object.keys(CODE_LANGUAGE)) assert.ok(registry.has(language), `${language} is a registry language`);
  // the map, never language-data's file-name matching, which disagrees with the registry
  assert.equal(codeLanguageName({ language: "matlab", name: "run.m" }), "Octave", ".m is MATLAB here, Mathematica there");
  assert.equal(codeLanguageName({ language: "ini", name: "app.cfg" }), "Properties files");
  assert.equal(codeLanguageName({ language: "latex", name: "main.tex" }), "LaTeX");
  assert.equal(codeLanguageName({ language: "cpp", name: "a.cc" }), "C++");
  assert.equal(codeLanguageName({ language: "jsx", name: "a.jsx" }), "JSX");
  assert.equal(codeLanguageName({ language: null, name: "notes.yaml", ext: ".bin" }), "YAML", "a name's own extension when the view has no language");
  assert.equal(codeLanguageName({ language: null, name: "run-local.bat", ext: ".bin" }), null, "a blocked extension: plain");
  assert.equal(codeLanguageName({ language: "plaintext", name: "notes.txt" }), null);
  assert.equal(codeLanguageName({ language: "makefile", name: "Makefile" }), null);
  assert.equal(codeLanguageName({ language: "csv", name: "d.csv" }), null);
  assert.equal(codeLanguageName({ language: "constructor", name: "" }), null, "an object member is no language");
  assert.equal(codeLanguageName(null), null);
  const opened = DOCUMENT_CASES.cases.find((c) => c.note.startsWith("a Markdown file read in place as text"));
  assert.equal(codeLanguageName(pageDocument({ properties: opened.properties })), "Markdown", "an opened .md is coloured as Markdown");
  assert.equal(codeLanguageName({ language: null, name: "README.md", ext: ".md" }), "Markdown", "by its name when the view has none");
});
