// One-off migration helper (docs/dev/i18n.md): wraps the suspects that
// tools/i18n-audit.mjs reports in t(), rewriting the exact syntax nodes.
//
//   node tools/i18n-wrap.mjs [files…]   # paths relative to src/; none = every file
//
// - JSX text            Save         -> {t("Save")}
// - JSX attribute       title="Save" -> title={t("Save")}
// - string literal      "Save"       -> t("Save")
// - template literal    `Saved ${n} page${n === 1 ? "" : "s"}.`
//                       -> t("Saved {n} page{_s}.", { n, _s: n === 1 ? "" : "s" })
// A placeholder whose name starts with "_" carries English-only grammar (the
// plural s); translations may leave it out.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { audit } from "./i18n-audit.mjs";

const require = createRequire(import.meta.url);
const parser = require("@babel/parser");
const traverse = require("@babel/traverse").default;
const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");

// Not interface text, or text that must stay English.
const SKIP_FILES = new Set([
  "editor/latexCompletion.js", "guide/anchors.js", "collaboration/linkName.js", "support/problemReport.js",
  "shared/lib/xhrUpload.js", "settings/assistantSetup.js", "app/prefDefs.js", "guide/triggers.js",
  "shared/lib/utils.js", "editor/codeHighlight.js", "guide/previewArea.js", "guide/previewHighlight.js",
  "ink/ink.js", "app/sessionState.js", "app/prefs.js", "collaboration/usePageCollab.js",
  "shared/model/blockModel.js", "library/libraryUtils.js", "editor/BlockCmEditor.jsx",
]);
// Literal text that must not be translated: key names, selectors, snippets
// inserted into notes, commands.
const SKIP_TEXT = /^(Mod-|button, a|\[data-|\| Column|```|> \[!|manage\.py|\[mcp_servers|gamma-backup-)/;
const SKIP_CALLS = /^(dbg|logSys|trace|log|debugLog|logDebug|console\.\w+|xhrUpload|downloadExport)$/;
const SKIP_PROPS = new Set(["prompt", "progressUrl", "viewerUrl", "endpoint", "url"]);
const URLISH = /^\{…\}\/|^\/|mode=|[?&]\w+=|^translate|^\(.*:|ease$|^gamma-/;

const lit = (s) => JSON.stringify(s);
const IDENT = /[A-Za-z_$][\w$]*/g;
const STOP = new Set(["length", "trim", "toFixed", "map", "join", "String", "Math", "Number", "round", "toLocaleString", "split", "pop", "slice", "filter", "find"]);

function nameFor(src, used) {
  const e = src.trim();
  let base = /^([A-Za-z_$][\w$]*)\.length$/.exec(e)?.[1] ? "n" : null;
  if (!base) {
    const ids = (e.match(IDENT) || []).filter((i) => !STOP.has(i) && !/^(true|false|null|undefined)$/.test(i));
    base = ids.length ? ids[ids.length - 1] : "value";
    base = base.replace(/^_+/, "") || "value";
  }
  let name = base, i = 2;
  while (used.has(name)) name = `${base}${i++}`;
  used.add(name);
  return name;
}

const isPluralS = (node) => node.type === "ConditionalExpression"
  && [node.consequent, node.alternate].every((b) => b.type === "StringLiteral" && ["", "s", "es"].includes(b.value));

function wrapTemplate(node, src) {
  const used = new Set(), args = [];
  let text = "";
  node.quasis.forEach((q, i) => {
    text += q.value.cooked.replace(/[{}]/g, (c) => (c === "{" ? "(" : ")"));
    const ex = node.expressions[i];
    if (!ex) return;
    const code = src.slice(ex.start, ex.end);
    let name;
    if (isPluralS(ex)) { name = "_s"; let k = 2; while (used.has(name)) name = `_s${k++}`; used.add(name); }
    else name = nameFor(code, used);
    args.push(name === code ? name : `${name}: ${code}`);
    text += `{${name}}`;
  });
  return `t(${lit(text)}, { ${args.join(", ")} })`;
}

function importPath(rel) {
  const depth = rel.split("/").length - 1;
  return `${"../".repeat(depth)}shared/i18n/i18n.js`;
}

function ensureImport(src, rel) {
  const re = /import \{([^}]*)\} from "([^"]*shared\/i18n\/i18n\.js)";/;
  const m = re.exec(src);
  if (m) {
    const names = m[1].split(",").map((s) => s.trim()).filter(Boolean);
    if (names.includes("t")) return src;
    return src.replace(re, `import { ${[...names, "t"].sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" })).join(", ")} } from "${m[2]}";`);
  }
  const imports = [...src.matchAll(/^import [^\n]*;\r?\n/gm)];
  const at = imports.length ? imports[imports.length - 1].index + imports[imports.length - 1][0].length : 0;
  const nl = src.includes("\r\n") ? "\r\n" : "\n";
  return src.slice(0, at) + `import { t } from "${importPath(rel)}";${nl}` + src.slice(at);
}

export function wrapFile(file, suspects) {
  const src = fs.readFileSync(file, "utf8");
  const rel = path.relative(SRC, file).split(path.sep).join("/");
  const ast = parser.parse(src, { sourceType: "module", plugins: ["jsx"] });
  const lines = new Set(suspects.map((s) => s.line));
  const edits = [];
  const covered = (n) => edits.some((e) => n.start >= e.start && n.end <= e.end);
  const skipByContext = (p) => {
    for (let prev = p.node, cur = p.parentPath; cur; prev = cur.node, cur = cur.parentPath) {
      const n = cur.node;
      if (n.type === "CallExpression") {
        const c = n.callee;
        const name = c.type === "Identifier" ? c.name : c.type === "MemberExpression" ? `${c.object.name || ""}.${c.property.name || ""}` : "";
        if (SKIP_CALLS.test(name)) return true;
        // the key (and tn's plural form) of a translation call; its
        // placeholder values are ordinary text
        if (/^(t|tn|T)$/.test(name) && n.arguments.indexOf(prev) !== -1 && n.arguments.indexOf(prev) < (name === "tn" ? 2 : 1)) return true;
      }
      if (n.type === "ObjectProperty" && SKIP_PROPS.has(n.key.name || n.key.value)) return true;
      if (n.type === "JSXAttribute" && /^(className|style|key)$/.test(n.name.name)) return true;
      if (n.type === "Program") break;
    }
    return false;
  };
  const settingsNav = rel === "settings/settingsNavigation.js";
  traverse(ast, {
    JSXText(p) {
      if (!lines.has(p.node.loc.start.line) || covered(p.node)) return;
      const raw = src.slice(p.node.start, p.node.end);
      const text = p.node.value.trim().replace(/\s+/g, " ");
      if (!text) return;
      const lead = raw.match(/^\s*/)[0], trail = raw.match(/\s*$/)[0];
      edits.push({ start: p.node.start, end: p.node.end, text: `${lead}{t(${lit(text)})}${trail}` });
    },
    StringLiteral(p) {
      const n = p.node;
      if (!lines.has(n.loc.start.line) || covered(n) || skipByContext(p)) return;
      if (!suspects.some((s) => s.line === n.loc.start.line && s.text === n.value.trim().replace(/\s+/g, " "))) return;
      if (URLISH.test(n.value) || SKIP_TEXT.test(n.value.trim())) return;
      if (settingsNav) {
        const arr = p.parentPath.node;
        if (arr.type !== "ArrayExpression" || arr.elements.indexOf(n) !== 1) return;
      }
      const parent = p.parentPath.node;
      const out = parent.type === "JSXAttribute" ? `{t(${lit(n.value)})}` : `t(${lit(n.value)})`;
      edits.push({ start: n.start, end: n.end, text: out });
    },
    TemplateLiteral(p) {
      const n = p.node;
      if (!lines.has(n.loc.start.line) || covered(n) || skipByContext(p)) return;
      if (p.parentPath.node.type === "TaggedTemplateExpression") return;
      const text = n.quasis.map((q) => q.value.cooked).join("{…}");
      if (!suspects.some((s) => s.line === n.loc.start.line && s.text === `\`${text}\``.replace(/\s+/g, " "))) return;
      if (URLISH.test(text) || SKIP_TEXT.test(text.trim())) return;
      edits.push({ start: n.start, end: n.end, text: wrapTemplate(n, src) });
    },
  });
  if (!edits.length) return 0;
  // Innermost edits lose to an enclosing one (it copies their source).
  edits.sort((a, b) => a.start - b.start || b.end - a.end);
  const keep = [];
  for (const e of edits) if (!keep.some((k) => e.start >= k.start && e.end <= k.end)) keep.push(e);
  let out = src;
  for (const e of keep.sort((a, b) => b.start - a.start)) out = out.slice(0, e.start) + e.text + out.slice(e.end);
  out = ensureImport(out, rel);
  // A parse check: never leave a file broken.
  parser.parse(out, { sourceType: "module", plugins: ["jsx"] });
  fs.writeFileSync(file, out);
  return keep.length;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const only = process.argv.slice(2).filter((a) => !a.startsWith("--"));
  const found = audit();
  const byFile = {};
  for (const f of found) (byFile[f.file] ||= []).push(f);
  let total = 0;
  for (const [rel, suspects] of Object.entries(byFile)) {
    if (SKIP_FILES.has(rel) || (only.length && !only.includes(rel))) continue;
    try {
      const n = wrapFile(path.join(SRC, rel), suspects);
      if (n) { console.log(`${String(n).padStart(4)} ${rel}`); total += n; }
    } catch (e) { console.log(`FAIL ${rel}: ${e.message}`); }
  }
  console.log(`${total} wrapped`);
}
