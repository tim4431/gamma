// What a disk that ignores case (Windows, and macOS by default) does to the
// repository: two tracked paths equal but for case are one file there, and
// an import without an extension can find another module than on Linux.
// Vite tries `.mjs`, `.js`, …, `.jsx` in turn, so `import "./TabGroups"` beside
// `tabGroups.js` and `TabGroups.jsx` found the `.js` on Windows and the `.jsx`
// on Linux, and the Windows build failed while CI passed. A module that shares
// its folder with another of the same name but for case is imported with its
// extension (frontend/src/README.md "Placement and naming").
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const SRC = path.join(ROOT, "frontend", "src");
// Vite's resolve.extensions, in its order (vite.config.js keeps the default).
const EXTENSIONS = [".mjs", ".js", ".mts", ".ts", ".jsx", ".tsx", ".json"];
const SOURCE = /\.(mjs|js|jsx|ts|tsx)$/;

// The file an import names, as Vite finds it: the path as written, then with
// each extension, then a folder's index. `entries(dir)` lists a folder (null
// when there is none); `fold` matches each part of the path regardless of
// case, as Windows and macOS do. Returns the path with its real spelling, or
// null when nothing matches.
function resolveImport(from, spec, entries, fold) {
  const bare = spec.replace(/[?#].*$/, "");
  const target = path.resolve(path.dirname(from), bare);
  const real = (abs) => {
    const parts = path.relative(ROOT, abs).split(path.sep);
    let at = ROOT;
    for (let i = 0; i < parts.length; i++) {
      const names = entries(at);
      if (!names) return null;
      const name = names.find((n) => n === parts[i]) ?? (fold ? names.find((n) => n.toLowerCase() === parts[i].toLowerCase()) : undefined);
      if (name === undefined) return null;
      at = path.join(at, name);
    }
    return at;
  };
  const isFile = (abs) => { const hit = real(abs); return hit && entries(hit) === null ? hit : null; };
  for (const candidate of [target, ...EXTENSIONS.map((ext) => target + ext), ...EXTENSIONS.map((ext) => path.join(target, `index${ext}`))]) {
    const hit = isFile(candidate);
    if (hit) return hit;
  }
  return null;
}

// Relative imports in a module's source: static, re-exports, side effects
// and dynamic. Comment lines are left out, as the i18n scan leaves them.
function relativeImports(source) {
  const code = source.split("\n").map((line) => (/^\s*(\/\/|\*|\/\*)/.test(line) ? "" : line)).join("\n");
  const out = [];
  for (const m of code.matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+)(["'])(\.{1,2}\/[^"'\n]+)\1/g)) out.push(m[2]);
  return out;
}

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (SOURCE.test(entry.name)) yield full;
  }
}

const listing = new Map();
const diskEntries = (dir) => {
  if (!listing.has(dir)) {
    let names = null;
    try { if (fs.statSync(dir).isDirectory()) names = fs.readdirSync(dir); } catch {}
    listing.set(dir, names);
  }
  return listing.get(dir);
};

test("the check finds what broke the Windows build, and lets an import with its extension through", () => {
  // frontend/src/workspace as it stood: the frames and the model beside them
  const fake = new Map([
    [ROOT, ["frontend"]], [path.join(ROOT, "frontend"), ["src"]], [SRC, ["app", "workspace"]],
    [path.join(SRC, "app"), ["App.jsx"]], [path.join(SRC, "workspace"), ["TabGroups.jsx", "tabGroups.js"]],
  ]);
  const entries = (dir) => fake.get(dir) ?? null;
  const app = path.join(SRC, "app", "App.jsx");
  assert.equal(resolveImport(app, "../workspace/TabGroups", entries, false), path.join(SRC, "workspace", "TabGroups.jsx"));
  assert.equal(resolveImport(app, "../workspace/TabGroups", entries, true), path.join(SRC, "workspace", "tabGroups.js"));
  for (const spec of ["../workspace/TabGroups.jsx", "../workspace/tabGroups.js"]) {
    assert.equal(resolveImport(app, spec, entries, true), resolveImport(app, spec, entries, false), spec);
  }
  // misspelt case: found only where case is ignored
  assert.equal(resolveImport(app, "../workspace/tabgroups.js", entries, false), null);
  assert.equal(resolveImport(app, "../Workspace/tabGroups.js", entries, true), path.join(SRC, "workspace", "tabGroups.js"));
  assert.deepEqual(relativeImports('import { a } from "./x";\n// import b from "./y";\nexport * from \'../z.js\';\nconst c = import("./w");\nimport "./s.css";'),
    ["./x", "../z.js", "./w", "./s.css"]);
});

test("every relative import in frontend/src names the same file whether or not the disk ignores case", () => {
  const wrong = [];
  for (const file of walk(SRC)) {
    for (const spec of relativeImports(fs.readFileSync(file, "utf8"))) {
      const linux = resolveImport(file, spec, diskEntries, false);
      const folded = resolveImport(file, spec, diskEntries, true);
      if (linux === folded) continue;
      const rel = (p) => (p ? path.relative(ROOT, p).split(path.sep).join("/") : "nothing");
      wrong.push(`${rel(file)}: "${spec}" is ${rel(linux)} on Linux but ${rel(folded)} on Windows and macOS`);
    }
  }
  assert.deepEqual(wrong, [], "give these imports their extension, or their file a name of its own");
});

test("no two tracked paths are equal but for case", (t) => {
  let tracked;
  try {
    tracked = execFileSync("git", ["ls-files", "-z"], { cwd: ROOT, encoding: "utf8", maxBuffer: 64 << 20 }).split("\0").filter(Boolean);
  } catch {
    t.skip("git is not available here");
    return;
  }
  const seen = new Map();
  const clashes = [];
  for (const p of tracked) {
    const key = p.toLowerCase();
    if (seen.has(key) && seen.get(key) !== p) clashes.push(`${seen.get(key)} and ${p}`);
    else seen.set(key, p);
  }
  assert.deepEqual(clashes, [], "a checkout on Windows or macOS keeps only one of each");
});
