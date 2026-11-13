// Which CodeMirror language the text viewer colours a document with: the
// registry's language word (formats.js) mapped to a description name of
// @codemirror/language-data, never its own file-name matching, which
// disagrees with the registry (.m is Mathematica there, .cfg TTCN, text
// LaTeX). Pure: tests/viewers.test.mjs holds every name to language-data's.
import { extensionOf, languageOf } from "./formats.js";

// A registry language → the description's name; a language with no mode
// (plain text, CSV, BibTeX, Typst, reStructuredText, a Makefile) is left
// out and shown plain.
export const CODE_LANGUAGE = {
  latex: "LaTeX", python: "Python", json: "JSON", yaml: "YAML", toml: "TOML", xml: "XML", ini: "Properties files",
  html: "HTML", javascript: "JavaScript", typescript: "TypeScript", jsx: "JSX", tsx: "TSX", css: "CSS", scss: "SCSS",
  c: "C", cpp: "C++", rust: "Rust", go: "Go", java: "Java", kotlin: "Kotlin", swift: "Swift", ruby: "Ruby", php: "PHP",
  shell: "Shell", r: "R", julia: "Julia", lua: "Lua", sql: "SQL", perl: "Perl", haskell: "Haskell", scala: "Scala",
  diff: "diff", matlab: "Octave", mathematica: "Mathematica", dockerfile: "Dockerfile", cmake: "CMake",
};

// The description name for a document: its language (the view's, from its
// stored extension and whole name), else the one its own name's extension
// says (a run-local.bat stored as .bin), else null for plain text.
export function codeLanguageName(doc) {
  const language = doc?.language || languageOf(doc?.name, extensionOf(doc?.name));
  return (language && Object.hasOwn(CODE_LANGUAGE, language) ? CODE_LANGUAGE[language] : null) || null;
}
