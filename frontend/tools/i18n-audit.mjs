// Lists interface text that never reaches t() (docs/dev/i18n.md).
//
//   node tools/i18n-audit.mjs            # every suspect, file:line  text  [context]
//   node tools/i18n-audit.mjs --count    # how many per file
//
// The catalog test only sees strings already wrapped; this parses every
// source file and reports prose-looking string and template literals that
// are not an argument of t / tn / T and not in a place that is clearly code
// (imports, object keys, comparisons, class names, URLs, storage keys,
// console and DOM calls). It is a heuristic: a report is a candidate to
// look at, and a line can opt out with an `i18n-ignore` comment.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const parser = require("@babel/parser");
const traverse = require("@babel/traverse").default;

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");

// Attributes / properties whose value is an identifier, never text.
const CODE_NAMES = new Set([
  "className", "key", "type", "id", "name", "role", "href", "src", "rel", "target", "style", "value",
  "method", "mode", "variant", "kind", "icon", "autoComplete", "inputMode", "accept", "download", "htmlFor",
  "lang", "dir", "tone", "placement", "anchor", "side", "state", "scope", "pane", "protocol", "endpoint",
  "format", "category", "view", "open", "when", "event", "action", "shape", "glyph", "keywords", "code",
  "color", "background", "fill", "stroke", "d", "viewBox", "transform", "path", "url", "cls", "tag",
  "sandbox", "allow", "loading", "decoding", "encType", "spellCheck", "autoCapitalize", "enterKeyHint",
  "pattern", "min", "max", "step", "width", "height", "size", "language", "status", "kindLabel",
]);
// Calls whose string arguments are code.
const CODE_CALLS = /^(console\.\w+|fetch|apiJson|localStorage\.\w+|sessionStorage\.\w+|\w*\.?querySelector(All)?|\w*\.?closest|\w*\.?addEventListener|\w*\.?removeEventListener|\w*\.?setAttribute|\w*\.?getAttribute|\w*\.?removeAttribute|\w*\.?getPropertyValue|\w*\.?setProperty|\w*\.?matchMedia|\w*\.?includes|\w*\.?startsWith|\w*\.?endsWith|\w*\.?split|\w*\.?join|\w*\.?replace(All)?|\w*\.?indexOf|\w*\.?lastIndexOf|\w*\.?has|\w*\.?get|\w*\.?set|\w*\.?delete|\w*\.?append|\w*\.?createElement|\w*\.?toggle|\w*\.?add|\w*\.?remove|\w*\.?contains|\w*\.?postMessage|\w*\.?dispatchEvent|\w*\.?emit|JSON\.\w+|encodeURIComponent|decodeURIComponent|new URL|URL|new RegExp|RegExp|require|import|usePersistedState|usePersistedFlag|pref|flag|guideEvent|trace|log|debugLog|\w*\.?isTypeSupported|\w*\.?getItem|\w*\.?setItem|\w*\.?removeItem|withWorkspace|withShare|assetUrl|t|tn|T)$/;

const prose = (text) => {
  const s = text.trim();
  if (s.length < 2 || !/[A-Za-z]{2}/.test(s)) return false;
  if (/^[a-z0-9_.:/#?&=@+-]+$/.test(s)) return false;            // identifiers, keys, paths
  if (/^[\w-]+(\s+[\w-]+)*$/.test(s) && !/[A-Z]/.test(s) && !s.includes(" ")) return false;
  if (/^(https?:|\/|\.|#|\$|--|[@&])/.test(s)) return false;     // URLs, paths, selectors, CSS vars
  if (/^[a-z][\w-]*( [a-z][\w-]*)+$/.test(s) && /-|[a-z][A-Z]/.test(s)) return false; // class lists
  if (/[{};]\s*$|^\s*[<{]|=>|\bfunction\b/.test(s)) return false; // code
  if (/^(rgba?|hsla?|var|calc|url)\(|^\d/.test(s)) return false;   // CSS values
  if (/^[A-Z_]+$/.test(s)) return false;                           // CONSTANTS
  return /[A-Z]/.test(s[0]) || /\s/.test(s) || /[….?!]$/.test(s);
};

function calleeName(node) {
  if (!node) return "";
  if (node.type === "Identifier") return node.name;
  if (node.type === "MemberExpression") return `${calleeName(node.object)}.${node.property.name || ""}`.replace(/^\./, "");
  if (node.type === "Import") return "import";
  return "";
}

function context(p) {
  // Walk out of wrappers that keep the string text: ternaries, logical ops, arrays.
  let cur = p;
  while (["ConditionalExpression", "LogicalExpression", "ArrayExpression", "SequenceExpression", "ParenthesizedExpression"]
    .includes(cur.parentPath?.node.type)) {
    const parent = cur.parentPath.node;
    if (parent.type === "ConditionalExpression" && parent.test === cur.node) return "code";
    cur = cur.parentPath;
  }
  const parent = cur.parentPath?.node;
  if (!parent) return "?";
  switch (parent.type) {
    case "ImportDeclaration": case "ExportNamedDeclaration": case "ExportAllDeclaration": return "code";
    case "BinaryExpression": return "code";
    case "SwitchCase": return "code";
    case "MemberExpression": return "code";
    case "ObjectProperty":
      if (parent.key === cur.node) return "code";
      if (CODE_NAMES.has(parent.key.name || parent.key.value)) return "code";
      return `prop ${parent.key.name || parent.key.value}`;
    case "JSXAttribute": {
      const name = parent.name.name?.name ? `${parent.name.namespace?.name}:${parent.name.name.name}` : parent.name.name;
      if (CODE_NAMES.has(name) || /^data-|^on[A-Z]/.test(name)) return "code";
      return `attr ${name}`;
    }
    case "JSXExpressionContainer": {
      const attr = cur.parentPath.parentPath?.node;
      if (attr?.type === "JSXAttribute") {
        const name = attr.name.name;
        if (CODE_NAMES.has(name) || /^data-|^on[A-Z]/.test(name)) return "code";
        return `attr ${name}`;
      }
      return "child";
    }
    case "CallExpression": case "NewExpression": {
      const name = calleeName(parent.callee);
      if (CODE_CALLS.test(name) || CODE_CALLS.test(name.split(".").pop())) return "code";
      if (/^(Error|TypeError)$/.test(name)) return "code";
      return `call ${name}`;
    }
    case "VariableDeclarator": return `const ${parent.id.name || ""}`;
    case "AssignmentExpression": return "assign";
    case "ReturnStatement": return "return";
    case "ArrowFunctionExpression": return "return";
    case "TemplateLiteral": return "code";
    case "TaggedTemplateExpression": return "code";
    default: return parent.type;
  }
}

function* files(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) { if (e.name !== "i18n") yield* files(full); }
    else if (/\.(jsx|js)$/.test(e.name)) yield full;
  }
}

export function audit(root = SRC) {
  const out = [];
  for (const file of files(root)) {
    const src = fs.readFileSync(file, "utf8");
    const lines = src.split(/\r?\n/);
    let ast;
    try { ast = parser.parse(src, { sourceType: "module", plugins: ["jsx"] }); }
    catch (e) { out.push({ file, line: 0, text: `PARSE ERROR ${e.message}`, ctx: "" }); continue; }
    const rel = path.relative(root, file).split(path.sep).join("/");
    const report = (node, text, ctx) => {
      const line = node.loc.start.line;
      if (/i18n-ignore/.test(lines[line - 1] || "")) return;
      out.push({ file: rel, line, text: text.trim().replace(/\s+/g, " "), ctx });
    };
    traverse(ast, {
      // Visible JSX text is interface text even as one lowercase word (a badge).
      JSXText(p) {
        const text = p.node.value;
        if (/[A-Za-z]{2}/.test(text) && !/^[\s·•—–|/×✓↑↓←→+-]*$/.test(text)) report(p.node, text, "text");
      },
      StringLiteral(p) {
        if (!prose(p.node.value)) return;
        const ctx = context(p);
        if (ctx !== "code") report(p.node, p.node.value, ctx);
      },
      TemplateLiteral(p) {
        const text = p.node.quasis.map((q) => q.value.cooked).join("{…}");
        const words = text.replace(/\{…\}/g, " ").match(/[A-Za-z]{2,}/g) || [];
        if (words.length < 2 || !prose(text.replace(/\{…\}/g, " x "))) return;
        if (/^[\s{}…]*[a-z0-9_-]*[\s{}…]*$/.test(text)) return;
        const ctx = context(p);
        if (ctx !== "code") report(p.node, `\`${text}\``, ctx);
      },
    });
  }
  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const found = audit();
  if (process.argv.includes("--count")) {
    const per = {};
    for (const f of found) per[f.file] = (per[f.file] || 0) + 1;
    for (const [f, n] of Object.entries(per).sort((a, b) => b[1] - a[1])) console.log(String(n).padStart(4), f);
    console.log(String(found.length).padStart(4), "total");
  } else {
    for (const f of found) console.log(`${f.file}:${f.line}  ${JSON.stringify(f.text)}  [${f.ctx}]`);
  }
}
