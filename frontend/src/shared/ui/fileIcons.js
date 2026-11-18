// A synced folder's file and directory icons (docs/dev/ui-design.md "File
// icons"): what kind of file or directory a name says, and how each kind is
// drawn — a mark on FileGlyph's sheet or in FolderKindIcon's folder
// (Icons.jsx), and a file's tint. FileIcon.jsx picks one by name; the
// design page's catalog (docs/design/assets/file-icons.svg) is drawn from
// the same tables by tools/designSheets.mjs. Pure: tests/fileIcons.test.mjs.
//
// A kind is a concept, shared by files and directories where both have it:
// "code" is a .py file and src/, "image" a .png and figures/, "tex" a .tex
// file and paper/. A name of no kind gets "": its page's format draws it.
// Each table lists the common names first, which the catalog shows.

// Whole file names, lowercased.
export const FILE_NAMES = {
  config: ["makefile", "dockerfile", "requirements.txt", "gnumakefile", "justfile", "cmakelists.txt", "containerfile",
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "procfile", "vagrantfile", "gemfile",
    "rakefile", "brewfile", "pipfile", "go.mod", "meson.build", "latexmkrc", "setup.cfg", ".editorconfig",
    ".gammaignore", ".dockerignore", ".npmignore", ".prettierignore", ".eslintignore", ".python-version",
    ".tool-versions"],
  git: [".gitignore", ".gitattributes", ".gitmodules", ".gitkeep", ".gitconfig", ".git-blame-ignore-revs", ".mailmap"],
  lock: ["package-lock.json", "pnpm-lock.yaml", "go.sum", "conda-lock.yml", "npm-shrinkwrap.json"],
  references: ["citation.cff"],
};

// File names before their first dot, lowercased, whatever follows:
// README, README.md and readme.txt alike.
export const FILE_STEMS = {
  readme: ["readme"],
  license: ["license", "licence", "copying", "unlicense", "notice"],
};

// Extensions, lowercased and without the dot; a dotted one ("tar.gz") is
// matched before its last part.
export const EXTENSIONS = {
  pdf: ["pdf"],
  doc: ["md", "txt", "docx", "rst", "markdown", "mdx", "mdc", "text", "org", "adoc", "asciidoc", "log", "rtf", "doc",
    "odt", "pages", "epub"],
  code: ["py", "js", "ts", "c", "cpp", "jl", "pyi", "pyx", "pxd", "mjs", "cjs", "jsx", "mts", "cts", "tsx", "vue",
    "svelte", "astro", "html", "htm", "xhtml", "xml", "xsl", "xsd", "css", "scss", "sass", "less", "h", "cc", "cxx",
    "hpp", "hh", "hxx", "ino", "cu", "cuh", "m", "mm", "rs", "go", "java", "kt", "kts", "scala", "groovy", "gradle",
    "clj", "cljs", "swift", "rb", "php", "pl", "pm", "r", "lua", "hs", "elm", "ex", "exs", "erl", "fs", "fsx", "ml",
    "mli", "nim", "zig", "dart", "cs", "vb", "f", "f90", "f95", "f03", "f08", "for", "sql", "wl", "wls", "sage", "v",
    "sv", "vhd", "vhdl", "asm", "s", "sol", "lisp", "el", "scm", "rkt", "tcl", "vim", "graphql", "gql", "proto",
    "glsl", "hlsl", "wgsl", "frag", "vert", "qasm", "qs", "jinja", "j2", "ejs", "hbs", "pug", "erb", "nix", "cmake",
    "mk"],
  shell: ["sh", "ps1", "bat", "bash", "zsh", "fish", "ksh", "csh", "command", "psm1", "cmd", "nu"],
  notebook: ["ipynb", "nb", "qmd", "mlx", "rmd"],
  tex: ["tex", "sty", "cls", "ltx", "latex", "dtx", "ins", "bst", "bbx", "cbx", "tikz", "pgf", "typ"],
  references: ["bib", "ris", "bbl", "bibtex", "enw", "nbib", "csl"],
  diff: ["diff", "patch", "rej"],
  table: ["csv", "tsv", "xlsx", "xls", "psv", "xlsm", "xlsb", "ods", "numbers"],
  data: ["h5", "npy", "mat", "parquet", "hdf5", "he5", "hdf", "nc", "nc4", "cdf", "npz", "pkl", "pickle", "joblib",
    "feather", "arrow", "avro", "orc", "fits", "fit", "fts", "root", "db", "sqlite", "sqlite3", "duckdb", "dat", "sav",
    "dta", "rds", "rdata", "rda", "pt", "pth", "ckpt", "safetensors", "onnx", "tfrecord", "h5ad", "msgpack", "bson",
    "shp", "gpkg", "qpy"],
  json: ["json", "jsonl", "geojson", "jsonc", "json5", "ndjson", "topojson", "webmanifest"],
  config: ["yaml", "toml", "ini", "yml", "cfg", "conf", "config", "properties", "env", "plist", "desktop", "service"],
  lock: ["lock", "pem", "key", "lockb", "crt", "cer", "p12", "pfx", "asc", "gpg"],
  archive: ["zip", "tar.gz", "7z", "tar", "gz", "tgz", "bz2", "tbz", "tbz2", "xz", "txz", "lz", "lzma", "zst", "rar",
    "whl", "egg", "jar", "war", "apk", "deb", "rpm", "dmg", "iso", "pkg", "msi", "snap", "appimage", "tar.bz2",
    "tar.xz", "tar.zst"],
  image: ["png", "jpg", "svg", "jpeg", "jpe", "jfif", "gif", "webp", "avif", "bmp", "ico", "icns", "svgz", "tif", "tiff",
    "heic", "heif", "jxl", "psd", "xcf", "ai", "eps", "raw", "cr2", "nef", "arw", "dng", "exr", "hdr", "tga", "pbm",
    "pgm", "ppm", "drawio", "excalidraw", "sketch", "fig", "kra"],
  // webm is a recording's audio, as documents/formats.js has it
  audio: ["mp3", "wav", "flac", "ogg", "oga", "opus", "m4a", "aac", "aif", "aiff", "wma", "mid", "midi", "amr", "webm"],
  video: ["mp4", "mov", "mkv", "m4v", "avi", "wmv", "flv", "mpg", "mpeg", "ogv", "3gp"],
  font: ["ttf", "otf", "woff2", "woff", "eot", "ttc", "pfb", "afm"],
  slides: ["pptx", "ppt", "odp", "pptm", "pps", "ppsx"],
  // what LaTeX leaves beside a paper, and compiled code: a plain sheet
  // ("synctex.gz" never "gz"'s archive)
  plain: ["aux", "synctex.gz", "exe", "o", "toc", "lof", "lot", "out", "fls", "fdb_latexmk", "bcf", "blg", "idx", "ind",
    "ilg", "nav", "snm", "vrb", "synctex", "run.xml", "dll", "so", "dylib", "a", "obj", "bin", "class", "pyc", "wasm"],
};

// Directory names, lowercased, without the dots and underscores that start
// or end them (".github" is "github", "__pycache__" "pycache").
export const FOLDER_NAMES = {
  doc: ["docs", "notes", "wiki", "doc", "documentation", "note", "manual", "manuals", "guide", "guides", "handbook",
    "memos", "minutes", "journal", "logbook"],
  tex: ["paper", "thesis", "latex", "manuscript", "manuscripts", "dissertation", "tex", "draft", "drafts", "article",
    "preprint", "proposal", "submission", "writing", "writeup", "chapters", "sections"],
  references: ["references", "refs", "papers", "reference", "ref", "bibliography", "bib", "literature", "readings",
    "reading", "citations"],
  code: ["src", "lib", "scripts", "source", "sources", "libs", "code", "app", "apps", "script", "bin", "tools", "tool",
    "utils", "util", "utilities", "helpers", "cmd", "internal", "core", "components", "api", "server", "client",
    "frontend", "backend", "hooks", "macros"],
  notebook: ["notebooks", "notebook", "ipynb"],
  test: ["tests", "experiments", "simulations", "test", "testing", "spec", "specs", "e2e", "experiment", "exp", "exps",
    "sim", "sims", "simulation", "runs", "lab", "sandbox", "playground"],
  data: ["data", "datasets", "raw", "dataset", "db", "database", "processed", "input", "inputs", "models",
    "checkpoints", "weights", "fixtures", "samples", "corpus", "measurements"],
  results: ["results", "output", "plots", "result", "outputs", "analysis", "analyses", "plot", "charts", "reports",
    "report", "metrics", "stats", "statistics", "benchmarks", "benchmark", "evaluation", "eval", "evals"],
  image: ["figures", "images", "assets", "image", "img", "imgs", "figure", "figs", "fig", "pictures", "pics", "photos",
    "photo", "screenshots", "icons", "graphics", "gfx", "art", "artwork", "diagrams", "drawings", "illustrations"],
  video: ["media", "videos", "recordings", "video", "audio", "music", "sounds", "movies", "clips", "animations"],
  slides: ["slides", "talks", "posters", "talk", "presentations", "presentation", "poster", "seminars", "lectures",
    "decks"],
  config: ["config", "settings", "vscode", "configs", "configuration", "conf", "idea", "devcontainer", "circleci",
    "claude", "cursor"],
  git: ["git", "github", "gitlab", "githooks", "husky"],
  packages: ["build", "dist", "node_modules", "venv", "vendor", "virtualenv", "site-packages", "packages", "deps",
    "dependencies", "third_party", "third-party", "external", "builds", "out", "target", "release", "releases", "next",
    "nuxt", "svelte-kit", "gradle", "tox", "nox"],
  temp: ["tmp", "cache", "pycache", "temp", "caches", "pytest_cache", "mypy_cache", "ruff_cache", "ipynb_checkpoints",
    "scratch", "trash", "logs", "log"],
  archive: ["legacy", "archive", "old", "backup", "archives", "archived", "backups", "bak", "deprecated", "attic",
    "obsolete", "previous", "history", "versions"],
  lock: ["secrets", "keys", "certs", "secret", "private", "certificates", "ssh", "gnupg", "credentials"],
};

const invert = (table) => Object.fromEntries(Object.entries(table).flatMap(([kind, keys]) => keys.map((k) => [k, kind])));
const BY_NAME = invert(FILE_NAMES);
const BY_STEM = invert(FILE_STEMS);
const BY_EXTENSION = invert(EXTENSIONS);
const BY_FOLDER = invert(FOLDER_NAMES);
const own = (table, key) => (Object.hasOwn(table, key) ? table[key] : "");

const leaf = (path) => String(path || "").split(/[\\/]/).filter(Boolean).at(-1)?.toLowerCase() || "";

// The kind of a file by its path or name: its whole name, then the name
// before its first dot (a README, a licence), a ".env" or a dotted ".…rc"
// file, then its longest dotted extension ("tar.gz" over "gz"; "index.d.ts"
// is "ts"'s), else "". `fileKind("paper/main.tex")` is "tex".
export function fileKind(path) {
  const name = leaf(path);
  const named = own(BY_NAME, name) || own(BY_STEM, name.replace(/\..*$/, ""));
  if (named) return named;
  if (/^\.env(\.|$)/.test(name) || /^\.[a-z0-9_-]+rc$/.test(name)) return "config";
  const parts = name.split(".");
  for (let i = 1; i < parts.length; i++) {
    const kind = own(BY_EXTENSION, parts.slice(i).join("."));
    if (kind) return kind;
  }
  return "";
}

// The kind of a directory by its path or name, "" for a folder of none.
// `folderKind("results")` is "results", `folderKind(".github")` "git".
export function folderKind(path) {
  return own(BY_FOLDER, leaf(path).replace(/^[._]+|[._]+$/g, ""));
}

// ---------- how each kind is drawn ----------

// The outlines on the 24 grid: FileGlyph's sheet and its folded corner,
// FolderKindIcon's folder (FolderIcon's), and the solid band a PDF's
// lettering becomes at a small size.
export const SHEET_PATH = "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z";
export const FOLD_PATH = "M14 2v6h6";
export const FOLDER_PATH = "M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z";
export const PDF_BAND = ["rect", { x: 7.5, y: 12.5, width: 9, height: 5, rx: 1, fill: "currentColor", stroke: "none" }];

// A folder's mark sits this much higher than a sheet's: the middle of the
// folder's body, not of the sheet under its fold.
export const FOLDER_MARK_SHIFT = -2;

const line = (d) => ["path", { d }];
const dot = (cx, cy, r) => ["circle", { cx, cy, r, fill: "currentColor" }];

// Each kind's mark as SVG elements, [tag, attributes], in the stroke of the
// icon around it. Drawn on the sheet in the box x 7.5–16.5, y 11–19 under
// the fold, and for the 16 px icon, whose 1.6 px stroke is 2.4 units: two
// strokes stay at least 3.5 units apart, a small round or a knob is a dot.
// "plain" is the bare sheet.
export const KIND_MARKS = {
  pdf: [], // its lettering, or PDF_BAND at a small size: FileGlyph's
  doc: [line("M8 13h8"), line("M8 17h5")],
  readme: [line("M12 11h.01"), line("M12 15v3.5")],
  license: [["circle", { cx: 12, cy: 13.5, r: 2.5 }], line("m10.3 15.4-.8 3.6 2.5-1.3 2.5 1.3-.8-3.6")],
  code: [line("M9.5 12 7.5 15l2 3"), line("m14.5 12 2 3-2 3")],
  shell: [line("m8 12 3 3-3 3"), line("M13 18h3")],
  notebook: [line("M8 13.5c2.2-2.4 5.8-2.4 8 0"), line("M8 17.5c2.2 2.4 5.8 2.4 8 0"), line("M16.5 10.5h.01")],
  tex: [line("M15.5 12h-7l3.5 3-3.5 3h7")],
  references: [dot(9.6, 13.4, 1.4), line("M11 13.4c0 2.2-1 3.6-2.8 4.3"), dot(14.6, 13.4, 1.4), line("M16 13.4c0 2.2-1 3.6-2.8 4.3")],
  diff: [line("M12 11v3.5"), line("M10.25 12.75h3.5"), line("M10.25 17.75h3.5")],
  table: [line("M8 13h2"), line("M14 13h2"), line("M8 17h2"), line("M14 17h2")],
  data: [["ellipse", { cx: 12, cy: 12.5, rx: 3.5, ry: 1.5 }], line("M8.5 12.5v5c0 .83 1.57 1.5 3.5 1.5s3.5-.67 3.5-1.5v-5")],
  json: [line("M10 12a1 1 0 0 0-1 1v1a1 1 0 0 1-1 1 1 1 0 0 1 1 1v1a1 1 0 0 0 1 1"),
    line("M14 18a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1 1 1 0 0 1-1-1v-1a1 1 0 0 0-1-1")],
  config: [line("M8 13h8"), line("M10 11.5v3"), line("M8 17h8"), line("M14 15.5v3")],
  lock: [["rect", { x: 8.5, y: 14, width: 7, height: 5, rx: 1 }], line("M10 14v-1a2 2 0 0 1 4 0v1")],
  git: [line("M9.5 11v8"), line("M9.5 17c3.5 0 5-1.5 5-4"), dot(14.5, 12, 1)],
  archive: [["rect", { x: 7.5, y: 11.5, width: 9, height: 2.5, rx: 0.5 }], line("M8.5 14v3.5a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1V14")],
  font: [line("M8.5 12h7"), line("M12 12v6.5")],
  slides: [["rect", { x: 7.5, y: 11.5, width: 9, height: 6, rx: 1 }], line("M12 17.5v2")],
  image: [line("M7 19l3.5-4.5 2.5 3 1.8-2.2L17 19"), ["circle", { cx: 14.5, cy: 12.5, r: 1.2 }]],
  audio: [line("M14 11v6.5"), ["circle", { cx: 12, cy: 17.5, r: 2 }], line("M14 11c0-1.5 1.5-2 3-1.5")],
  video: [["path", { d: "M10 12.5v5l4.5-2.5z", fill: "currentColor" }]],
  plain: [],
  // the directories' own
  test: [line("M10.5 11v2.6l-2.6 4.1a.9.9 0 0 0 .8 1.3h6.6a.9.9 0 0 0 .8-1.3l-2.6-4.1V11"), line("M9.8 11h4.4")],
  results: [line("M8.5 18.5v-3"), line("M12 18.5v-7"), line("M15.5 18.5v-5")],
  packages: [line("M12 11 8.5 13v4l3.5 2 3.5-2v-4z"), line("m8.5 13 3.5 2 3.5-2"), line("M12 15v4")],
  temp: [["circle", { cx: 12, cy: 15, r: 3.6 }], line("M12 13.2V15l1.2 1")],
};

// A file kind's tint (library.css .fileGlyph): a PDF its own red, code and
// TeX the accent, tables and datasets the data green, pictures, audio and
// video the warn amber; the rest (notes, config, archives…) muted. A
// directory is always the accent, as a library folder is.
export const KIND_TONES = {
  pdf: "pdf", code: "code", shell: "code", notebook: "code", tex: "code", diff: "code",
  table: "data", data: "data", json: "data", image: "media", audio: "media", video: "media",
};

// A page's document format (documents/formats.js FORMATS) as a kind, for
// a page without a file name of a kind: a text file is code, a binary the
// plain sheet, a note (markdown, or no document) its lines.
export const FORMAT_KINDS = { pdf: "pdf", markdown: "doc", text: "code", image: "image", audio: "audio", video: "video", binary: "plain" };

// The kind a sheet draws: `kind` when it is one, else `format`'s, else a
// note's. `glyphKind("", "text")` is "code".
export const glyphKind = (kind, format) => (Object.hasOwn(KIND_MARKS, kind) ? kind : own(FORMAT_KINDS, format)) || "doc";
