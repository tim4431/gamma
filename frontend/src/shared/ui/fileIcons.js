// A synced folder's file or directory icon, as VS Code picks it with
// Material Icon Theme (docs/dev/ui-design.md "File icons"). The theme's map
// is the virtual module frontend/tools/fileIcons.mjs makes, read in a lazy
// chunk the first time an icon is drawn; FileIcon.jsx draws one. iconFor is
// pure: tests/fileIcons.test.mjs.

// VS Code's built-in languages whose files reach an icon of the theme's
// languageIds that no file name or extension of the theme gives:
// [language id, {extensions, filenames, patterns}], from its extensions'
// package.json files. A thirteenth, log's "*.log.?", is a literal that never
// matches. VS Code also guesses a language from a file's first line; Gamma
// matches by the path alone.
const LANGUAGES = [
  ["ignore", { extensions: [".gitignore_global", ".gitignore", ".git-blame-ignore-revs", ".npmignore"], patterns: [".copilotignore"] }],
  ["properties", { extensions: [".gitattributes", ".gitconfig", ".gitmodules", ".editorconfig"], filenames: ["gitconfig"],
    patterns: ["**/.config/git/config", "**/.git/config"] }],
  ["json", { extensions: [".bowerrc", ".jscsrc", ".jslintrc", ".vuerc"] }],
  ["jsonc", { extensions: [".eslintrc", ".jsfmtrc", ".jshintrc", ".hintrc", ".babelrc"],
    patterns: ["**/.github/hooks/*.json", "tsconfig.*.json", "jsconfig.*.json", "tsconfig-*.json", "jsconfig-*.json"] }],
  ["makefile", { filenames: ["OCamlMakefile"] }],
  ["dockerfile", { patterns: ["Dockerfile.*", "Containerfile.*"] }],
  ["groovy", { patterns: ["Jenkinsfile*"] }],
  ["cpp", { extensions: [".mpp", ".mxx"] }],
  ["vb", { extensions: [".brs"] }],
  ["markdown", { patterns: ["**/.cursor/**/*.mdc"] }],
  ["instructions", { patterns: ["**/.claude/rules/**/*.md"] }],
  ["git-rebase", { patterns: ["**/rebase-merge/done"] }],
];

// A glob as VS Code's associations write them: `**/` any directories,
// `*` and `?` within one name.
function globRegex(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      i++;
      if (glob[i + 1] === "/") { i++; re += "(?:.*/)?"; } else re += ".*";
    } else if (c === "*") re += "[^/]*";
    else if (c === "?") re += "[^/]";
    else re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${re}$`);
}

const ASSOCIATIONS = LANGUAGES.flatMap(([id, { extensions = [], filenames = [], patterns = [] }]) => [
  ...extensions.map((e) => ({ id, kind: "extension", key: e.toLowerCase() })),
  ...filenames.map((f) => ({ id, kind: "filename", key: f.toLowerCase() })),
  ...patterns.map((p) => ({ id, kind: "pattern", key: p.toLowerCase(), re: globRegex(p.toLowerCase()), path: p.includes("/") })),
]);

// The language VS Code gives a file by its path (lowercased), "" for none
// of LANGUAGES': its whole name, else the longest pattern that matches (one
// with a "/" against the whole path), else the longest extension.
export function languageOf(path) {
  const name = path.split("/").at(-1);
  let pattern = null, extension = null;
  for (const a of ASSOCIATIONS) {
    if (a.kind === "filename" && a.key === name) return a.id;
    if (a.kind === "pattern" && a.re.test(a.path ? path : name) && (!pattern || a.key.length > pattern.key.length)) pattern = a;
    if (a.kind === "extension" && name.endsWith(a.key) && (!extension || a.key.length > extension.key.length)) extension = a;
  }
  return (pattern || extension)?.id || "";
}

// An open folder's icon: folder-x-open, or folder-x-open_light.
const opened = (id) => (id.endsWith("_light") ? `${id.slice(0, -6)}-open_light` : `${id}-open`);

// The icon id of `path` (a file's or directory's path in its synced folder,
// its name alone at least) in `map` (the compact map), as VS Code's
// stylesheet ranks it: the case ignored, a key "parent/name" only under
// that parent and over the same key without it, the light theme's tables
// over the dark's (`light`). A file: its whole name over any extension,
// the longest dotted extension over a shorter one ("d.ts" over "ts"),
// then its language (LANGUAGES), else the default. A directory (`dir`):
// its name, else the default folder, `open` its open icon; the synced
// folder's own root (`root`) always the root folder's. `iconFor(map,
// "main.tex")` is "tex".
export function iconFor(map, path, { dir = false, open = false, root = false, light = false } = {}) {
  const parts = String(path || "").toLowerCase().split("/").filter(Boolean);
  const name = parts.at(-1) || "";
  const parent = parts.length > 1 ? parts.at(-2) : "";
  const tiers = light ? [[map.light, 1], [map, 0]] : [[map, 0]];
  if (root) return open ? opened(map.rootFolder) : map.rootFolder;
  if (dir) {
    for (const key of parent ? [`${parent}/${name}`, name] : [name]) {
      for (const [tier] of tiers) {
        const id = tier.folderNames?.[key];
        if (id) return open ? opened(id) : id;
      }
    }
    return open ? opened(map.folder) : map.folder;
  }
  let best = "", score = 0;
  const consider = (id, s) => { if (id && s > score) { best = id; score = s; } };
  const language = languageOf(parts.join("/"));
  const segs = name.split(".");
  for (const [tier, bonus] of tiers) {
    const look = (table, key, s) => {
      consider(table?.[key], s + bonus);
      if (parent) consider(table?.[`${parent}/${key}`], s + 1 + bonus);
    };
    look(tier.fileNames, name, 4 + segs.length - 1);
    if (name.length <= 255) for (let i = 1; i < segs.length; i++) look(tier.fileExtensions, segs.slice(i).join("."), segs.length - i + 2);
    if (language) consider(tier.languageIds?.[language], 2 + bonus);
  }
  return best || map.file;
}

// ---------- the map, read once ----------

// The icons as FileIcon.jsx reads them: null while the map is read, {map,
// version, light} once it is in, `light` whether the page's scheme
// (data-scheme, app/theme.js) is light, and {failed: true} for good when its
// chunk did not load. A store read through useSyncExternalStore: the first
// subscriber starts the read and watches the scheme.
let icons = null;
let started = false;
const listeners = new Set();
const tell = () => { for (const fn of listeners) fn(); };
const lightNow = () => document.documentElement.dataset.scheme !== "dark";

function start() {
  if (started) return;
  started = true;
  import("virtual:file-icons").then(
    (mod) => { icons = { map: mod.default, version: mod.version, light: lightNow() }; tell(); },
    () => { icons = { failed: true }; tell(); },
  );
  new MutationObserver(() => {
    if (icons?.map && icons.light !== lightNow()) { icons = { ...icons, light: lightNow() }; tell(); }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-scheme"] });
}

export function subscribeIcons(fn) {
  start();
  listeners.add(fn);
  return () => listeners.delete(fn);
}
export const iconsNow = () => icons;

// Where an icon is served: the build's dist/assets/file-icons/<version>/.
export const iconUrl = (version, id) => `/assets/file-icons/${version}/${id}.svg`;
