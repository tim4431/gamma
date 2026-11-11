// The format registry by name (docs/dev/documents.md): what a file is, read
// from its name. The twin of gamma/documents/formats.py for names only, since
// the client never sees bytes and never serves files; tests/shared/
// formats.json holds both to the same answers.

// What a page's document can be (doc.format).
export const FORMATS = ["pdf", "markdown", "text", "image", "audio", "video", "binary"];

// A stored extension: "." and 1-12 lowercase letters or digits.
export const EXTENSION_RE = /^\.[a-z0-9]{1,12}$/;

// Executables, refused as uploads (the server's set).
export const BLOCKED_EXTENSIONS = new Set([
  ".exe", ".com", ".scr", ".pif", ".bat", ".cmd", ".msi", ".msix", ".appx",
  ".dll", ".cpl", ".sys", ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".hta",
  ".ps1", ".psm1", ".reg", ".lnk", ".jar", ".app",
]);

// The extensions a format is known by, a text one with the language its
// contents are read as; any other extension is binary.
const TABLE = {
  pdf: [".pdf"],
  markdown: [".md", ".markdown"],
  text: {
    plaintext: [".txt", ".log"], csv: [".csv"], tsv: [".tsv"], json: [".json", ".jsonl", ".ipynb"],
    yaml: [".yaml", ".yml"], toml: [".toml"], xml: [".xml"], ini: [".ini", ".cfg", ".conf"],
    latex: [".tex", ".ltx", ".cls", ".sty"], bibtex: [".bib"], python: [".py"], mathematica: [".nb"],
    matlab: [".m"], html: [".html"], javascript: [".js", ".mjs", ".cjs"], typescript: [".ts"], jsx: [".jsx"],
    tsx: [".tsx"], css: [".css"], scss: [".scss"], c: [".c", ".h"], cpp: [".cc", ".cpp", ".cxx", ".hpp", ".hh"],
    rust: [".rs"], go: [".go"], java: [".java"], kotlin: [".kt"], swift: [".swift"], ruby: [".rb"], php: [".php"],
    shell: [".sh", ".bash", ".zsh"], r: [".r"], julia: [".jl"], lua: [".lua"], sql: [".sql"], perl: [".pl"],
    haskell: [".hs"], scala: [".scala"], diff: [".diff", ".patch"], rst: [".rst"], typst: [".typ"],
  },
  image: [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"],
  audio: [".mp3", ".wav", ".webm", ".m4a", ".ogg", ".opus"],
  video: [".mp4", ".mov", ".m4v"],
  binary: [".bin", ".ink", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt", ".ods",
    ".zip", ".gz", ".tgz", ".tar", ".7z", ".whl", ".h5", ".hdf5"],
};

// Extension -> {format, language?}.
export const EXTENSIONS = {};
for (const [format, exts] of Object.entries(TABLE)) {
  if (Array.isArray(exts)) for (const ext of exts) EXTENSIONS[ext] = { format };
  else for (const [language, list] of Object.entries(exts)) for (const ext of list) EXTENSIONS[ext] = { format, language };
}

// Whole file names (lowercased) -> [format, language]: the files known by
// their name rather than an extension.
export const NAMES = {
  makefile: ["text", "makefile"], gnumakefile: ["text", "makefile"], justfile: ["text", "makefile"],
  dockerfile: ["text", "dockerfile"], containerfile: ["text", "dockerfile"],
  "cmakelists.txt": ["text", "cmake"],
  rakefile: ["text", "ruby"], gemfile: ["text", "ruby"],
  license: ["text", "plaintext"], licence: ["text", "plaintext"], copying: ["text", "plaintext"],
  readme: ["text", "plaintext"], authors: ["text", "plaintext"], changelog: ["text", "plaintext"],
  ".gitignore": ["text", "plaintext"], ".gitattributes": ["text", "plaintext"],
  ".gammaignore": ["text", "plaintext"], ".dockerignore": ["text", "plaintext"],
  ".editorconfig": ["text", "ini"], ".env": ["text", "shell"],
};

// The whitespace Python's str.strip removes, which String.trim does not
// match exactly (it keeps \x1c-\x1f and \x85, and strips ﻿): the server
// reads every name, hash and address of a document stripped of this.
const SPACE = "[\\t-\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
const EDGE_SPACE = new RegExp(`^${SPACE}+|${SPACE}+$`, "g");
export const strip = (text) => String(text).replace(EDGE_SPACE, "");

const leafOf = (name) => strip(String(name || "").split(/[\\/]/).pop());
const own = (table, key) => (Object.hasOwn(table, key) ? table[key] : undefined);

// A name's last suffix, lowercased ("" when it has none: no dot after the
// first character, or a malformed one). No blocked check.
export function extensionOf(name) {
  const text = String(name || "");
  const dot = text.lastIndexOf(".");
  const ext = dot > 0 ? text.slice(dot).toLowerCase() : "";
  return EXTENSION_RE.test(ext) ? ext : "";
}

// The extension a file of this name is stored under: its own, ".bin" when it
// has none; null for a blocked one (the server refuses the upload).
export function storedExtension(name) {
  const ext = extensionOf(name);
  if (BLOCKED_EXTENSIONS.has(ext)) return null;
  return ext || ".bin";
}

// What a file of this name is: {format, language} (language null when its
// format has none).
export function formatOf(name) {
  const named = own(NAMES, leafOf(name).toLowerCase());
  if (named) return { format: named[0], language: named[1] };
  const row = own(EXTENSIONS, extensionOf(name));
  if (row) return { format: row.format, language: row.language || null };
  return { format: "binary", language: null };
}

// The language a text document's name or extension says, else null.
export function languageOf(name, ext) {
  const named = own(NAMES, leafOf(name).toLowerCase());
  if (named) return named[1];
  const row = own(EXTENSIONS, ext);
  return row?.format === "text" ? row.language : null;
}
