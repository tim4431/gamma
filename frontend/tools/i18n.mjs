// Keeps the interface-text catalogs complete (docs/dev/i18n.md).
//
//   node tools/i18n.mjs          # report: missing, empty and orphan keys per catalog
//   node tools/i18n.mjs --sync   # add missing keys as "" and drop orphans, keys sorted
//
// A key is the first string literal of a t("…"), tn("…", "…", n) or T("…")
// call anywhere under src/. tests/i18n.test.mjs runs the same scan and
// fails while a catalog is incomplete, so a new string is translated in
// the same change that adds it.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const SRC = path.join(FRONTEND, "src");
export const LOCALES_DIR = path.join(SRC, "shared", "i18n", "locales");

const STRING = String.raw`"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'`;
// t(, tn(, T( not preceded by a word character, a dot or a $ (so `.at(`,
// `split(`, `PT(` never match).
const CALL = new RegExp(String.raw`(?<![\w.$])(t|tn|T)\(\s*(${STRING})(?:\s*,\s*(${STRING}))?`, "g");

// The value of a JS string literal (single-quoted ones re-quoted for JSON).
const unquote = (literal) => JSON.parse(literal[0] === "'"
  ? `"${literal.slice(1, -1).replace(/\\'/g, "'").replace(/"/g, '\\"')}"`
  : literal);

function* sourceFiles(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* sourceFiles(full);
    else if (/\.(jsx?|mjs)$/.test(entry.name)) yield full;
  }
}

// key → { plural?: the "other" form of a tn(), files: [relative path, …] }
export function scanSources(root = SRC) {
  const keys = new Map();
  for (const file of sourceFiles(root)) {
    // Comment lines are not scanned (the module's own examples).
    const source = fs.readFileSync(file, "utf8").split("\n").map((line) => (/^\s*(\/\/|\*|\/\*)/.test(line) ? "" : line)).join("\n");
    const rel = path.relative(root, file).split(path.sep).join("/");
    for (const match of source.matchAll(CALL)) {
      const [, fn, first, second] = match;
      if (fn === "tn" && !second) throw new Error(`${rel}: tn() needs two string literals: ${match[0]}`);
      const key = unquote(first);
      const entry = keys.get(key) || { files: [] };
      if (fn === "tn") entry.plural = unquote(second);
      if (!entry.files.includes(rel)) entry.files.push(rel);
      keys.set(key, entry);
    }
  }
  return keys;
}

export const placeholders = (text) => [...String(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

// The problems of one catalog against the scanned keys, as messages.
export function checkCatalog(catalog, keys) {
  const problems = [];
  for (const [key, entry] of keys) {
    if (!(key in catalog)) { problems.push(`missing: ${JSON.stringify(key)} (${entry.files.join(", ")})`); continue; }
    const value = catalog[key];
    const isForms = value !== null && typeof value === "object";
    const forms = isForms ? Object.values(value) : [value];
    if (forms.some((form) => typeof form !== "string") || (isForms && !("other" in value))) {
      problems.push(`bad value: ${JSON.stringify(key)} must be a string or {category: string} with "other"`);
    } else if (forms.some((form) => !form)) {
      problems.push(`untranslated: ${JSON.stringify(key)}`);
    } else {
      const want = new Set(placeholders(key));
      if (entry.plural) { want.add("n"); for (const p of placeholders(entry.plural)) want.add(p); }
      for (const form of forms) {
        const got = new Set(placeholders(form));
        if (entry.plural) got.add("n");
        if ([...got].sort().join() !== [...want].sort().join()) problems.push(`placeholders differ: ${JSON.stringify(key)} → ${JSON.stringify(form)}`);
      }
    }
  }
  for (const key of Object.keys(catalog)) if (!keys.has(key)) problems.push(`orphan: ${JSON.stringify(key)}`);
  return problems;
}

export function readCatalog(locale) {
  return JSON.parse(fs.readFileSync(path.join(LOCALES_DIR, `${locale}.json`), "utf8"));
}

export const catalogLocales = () => fs.readdirSync(LOCALES_DIR).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5));

function sync(locale, keys) {
  const old = readCatalog(locale);
  const next = {};
  for (const key of [...keys.keys()].sort()) next[key] = key in old ? old[key] : "";
  fs.writeFileSync(path.join(LOCALES_DIR, `${locale}.json`), JSON.stringify(next, null, 2) + "\n");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const keys = scanSources();
  const doSync = process.argv.includes("--sync");
  let bad = 0;
  for (const locale of catalogLocales()) {
    if (doSync) sync(locale, keys);
    const problems = checkCatalog(readCatalog(locale), keys);
    console.log(`${locale}: ${keys.size} keys, ${problems.length} problem(s)`);
    for (const p of problems) console.log(`  ${p}`);
    bad += problems.length;
  }
  process.exit(bad ? 1 : 0);
}
