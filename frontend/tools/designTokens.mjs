// Counts the raw design values left in the stylesheets — the ones that
// should come from tokens.css (docs/dev/ui-design.md, "Tokens") — for the
// ratchet in tests/designTokens.test.mjs.
//
//   node tools/designTokens.mjs              # totals per kind and per file
//   node tools/designTokens.mjs --list z     # every offender of one kind (color, size, radius, shadow, z, duration)
//   node tools/designTokens.mjs --baseline   # rewrite tests/design-baseline.json
//
// What counts, per declaration, in every .css under src/ except tokens.css:
//   color     a colour literal (#hex, rgb(), hsl()) — colours are theme tokens
//             (a mask's gradient is alpha, not colour)
//   size      a font size in px/pt/rem that isn't a var(--fs-*) (em, %, inherit are fine)
//   radius    a corner radius over 2 px that isn't a var(--radius-*) (0, % and
//             a 1-2 px hairline bar are fine)
//   shadow    an elevation shadow (a blur, not inset) that isn't a var(--shadow-*);
//             rings (0 0 0 Npx) and inset bars are borders, not depth
//   z         a z-index above 9 that isn't a var(--z-*); 1-9 orders siblings in one component
//   duration  a transition/animation time under 500 ms that isn't a var(--dur-*);
//             loops and attention effects of half a second or more keep their own period
//
// A justified raw value carries a comment: `ds-allow: why` in a comment on
// the declaration's line exempts that line, and `ds-allow-start: why` …
// `ds-allow-end` in comments exempts every line between (a highlight
// palette, the collaborator colours, a decorative drawing).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(FRONTEND, "src");
export const BASELINE = path.join(FRONTEND, "tests", "design-baseline.json");
export const KINDS = ["color", "size", "radius", "shadow", "z", "duration"];
const TOKENS = path.join("shared", "styles", "tokens.css");

export function cssFiles(dir = SRC) {
  const out = [];
  (function walk(d) {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".css") && path.relative(SRC, p) !== TOKENS) out.push(p);
    }
  })(dir);
  return out.sort();
}

// Declarations with their line, and whether an allow comment covers them.
export function declarations(css) {
  const lines = css.split(/\r?\n/);
  const allowed = new Set();
  let inBlock = false;
  lines.forEach((line, i) => {
    if (/\bds-allow-start\b/.test(line)) inBlock = true;
    if (inBlock || /\bds-allow:/.test(line)) allowed.add(i + 1);
    if (/\bds-allow-end\b/.test(line)) inBlock = false;
  });
  // Blank out comments, keeping line breaks so offsets map to lines.
  const text = css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
  const lineAt = (i) => (text.slice(0, i).match(/\n/g) || []).length + 1;
  const out = [];
  let depth = 0, start = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === "{") { depth++; start = i + 1; continue; }
    if (ch !== ";" && ch !== "}") continue;
    const d = text.slice(start, i);
    const m = depth > 0 && d.match(/^(\s*)([-\w]+)\s*:\s*([\s\S]+?)\s*$/);
    if (m) {
      const at = lineAt(start + m[1].length);
      out.push({ prop: m[2].toLowerCase(), value: m[3].replace(/\s+/g, " "), line: at, allowed: allowed.has(at) });
    }
    if (ch === "}") depth = Math.max(0, depth - 1);
    start = i + 1;
  }
  return out;
}

const COLOR = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?)\(/gi;
const splitTop = (s) => {
  const parts = []; let depth = 0, cur = "";
  for (const ch of s) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (ch === "," && !depth) { parts.push(cur); cur = ""; } else cur += ch;
  }
  return parts.concat(cur).map((p) => p.trim()).filter(Boolean);
};

// The kinds of raw value one declaration holds (a kind may count twice).
export function offences({ prop, value }) {
  const hits = [];
  if (!/mask/.test(prop)) for (const _ of value.matchAll(COLOR)) hits.push("color");
  if (prop === "font-size" || prop === "font") {
    // the font shorthand's "/ line-height" is not a size
    const v = value.replace(/\/\s*[^\s]+/g, "").replace(/var\(--fs-[\w-]+\)/g, "");
    if (/(^|[\s(])[\d.]+(px|pt|rem)\b/.test(v)) hits.push("size");
  }
  if (/^border(-(top|bottom)-(left|right))?-radius$/.test(prop)) {
    const v = value.replace(/var\(--radius-[\w-]+\)/g, "");
    if ([...v.matchAll(/(?:^|[\s(])([\d.]+)px\b/g)].some(([, n]) => +n > 2)) hits.push("radius");
  }
  if (prop === "box-shadow" && !/^(none|inherit|initial|unset)$/.test(value)) {
    for (const s of splitTop(value)) {
      if (/var\(--shadow-/.test(s) || /\binset\b/.test(s)) continue;
      const lengths = s.replace(/\b(?:rgba?|hsla?|color-mix|var)\([^)]*\)+/g, " ").match(/-?[\d.]+(px)?\b/g) || [];
      if (lengths.length >= 3 && parseFloat(lengths[2]) > 0) hits.push("shadow");
    }
  }
  if (prop === "z-index") {
    const n = Number(value);
    if (Number.isFinite(n) && n > 9) hits.push("z");
  }
  if (/^(transition|animation)(-duration)?$/.test(prop)) {
    for (const [, num, unit] of value.matchAll(/(?<![\w.-])(\d*\.?\d+)(ms|s)\b/g)) {
      const ms = unit === "s" ? +num * 1000 : +num;
      if (ms > 0 && ms < 500) hits.push("duration");
    }
  }
  return hits;
}

// { file: { kind: [ "line: prop: value", … ] } } over every stylesheet.
export function scan(files = cssFiles()) {
  const found = {};
  for (const f of files) {
    const rel = path.relative(SRC, f).split(path.sep).join("/");
    for (const d of declarations(fs.readFileSync(f, "utf8"))) {
      if (d.allowed) continue;
      for (const kind of offences(d)) {
        ((found[rel] ||= {})[kind] ||= []).push(`${d.line}: ${d.prop}: ${d.value}`);
      }
    }
  }
  return found;
}

// Counts per file and kind, the shape of the baseline.
export function counts(found = scan()) {
  const out = {};
  for (const [file, kinds] of Object.entries(found).sort()) {
    out[file] = Object.fromEntries(KINDS.filter((k) => kinds[k]).map((k) => [k, kinds[k].length]));
  }
  return out;
}

export const totals = (c) => Object.fromEntries(KINDS.map((k) => [k, Object.values(c).reduce((n, f) => n + (f[k] || 0), 0)]));

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const found = scan();
  const c = counts(found);
  if (args[0] === "--baseline") {
    fs.writeFileSync(BASELINE, JSON.stringify(c, null, 2) + "\n");
    console.log(`wrote ${path.relative(FRONTEND, BASELINE)}`, totals(c));
  } else if (args[0] === "--list") {
    const kind = args[1];
    for (const [file, kinds] of Object.entries(found)) {
      for (const [k, list] of Object.entries(kinds)) {
        if (!kind || k === kind) for (const x of list) console.log(`${file}:${x}  [${k}]`);
      }
    }
  } else {
    console.log(totals(c));
    for (const [file, kinds] of Object.entries(c)) console.log(`  ${file}`, kinds);
  }
}
