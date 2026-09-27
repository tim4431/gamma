// Resolves the design tokens (src/shared/styles/tokens.css) the way a
// browser would for one theme, and measures the theme's text contrast
// (docs/dev/ui-design.md, "Tokens").
//
//   node tools/themes.mjs              # every theme's contrast table, failures marked
//   node tools/themes.mjs --baseline   # rewrite tests/theme-contrast-baseline.json
//
// tokens.css is a flat list of rules on the root element, so a small
// evaluator is enough: selectors made of :root, :where(), :not() and
// [data-theme]/[data-scheme] attributes (an element or class selector never
// matches the root); the cascade by specificity, then
// order; var() with fallbacks; hex, rgb() and color-mix() in oklab or srgb
// with premultiplied alpha. tests/themes.test.mjs runs the contrast ratchet
// on top of it.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const TOKENS_CSS = path.join(FRONTEND, "src", "shared", "styles", "tokens.css");
export const BASELINE = path.join(FRONTEND, "tests", "theme-contrast-baseline.json");

// The pinned themes and the scheme each one sets as data-scheme — the same
// split as DARK_THEMES in src/app/prefDefs.js (the test keeps them equal).
export const THEME_SCHEMES = {
  light: "light", dark: "dark", "gamma-light": "light", "gamma-dark": "dark",
  sepia: "light", solarized: "light", gray: "light",
};

// What the ratchet measures: text on the surfaces it sits on (WCAG AA 4.5:1),
// faint text 3:1, and the primary button's label on its fill.
export const PAIRS = [
  ["--text-primary", "--bg-surface", 4.5],
  ["--text-primary", "--bg-page", 4.5],
  ["--text-secondary", "--bg-surface", 4.5],
  ["--text-tertiary", "--bg-surface", 4.5],
  ["--text-muted", "--bg-surface", 4.5],
  ["--text-muted", "--bg-page", 4.5],
  ["--text-muted", "--bg-hover", 4.5],
  ["--text-dim", "--bg-surface", 3],
  ["--accent", "--bg-surface", 4.5],
  ["--accent", "--bg-page", 4.5],
  ["--on-accent", "--accent-dark", 4.5],
  ["--danger", "--bg-surface", 4.5],
];

// ---- parsing -------------------------------------------------------------

export function parseRules(css) {
  css = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css))) {
    const decls = {};
    for (const d of m[2].split(";")) {
      const i = d.indexOf(":");
      if (i > 0) decls[d.slice(0, i).trim()] = d.slice(i + 1).trim();
    }
    rules.push({ selectors: splitTop(m[1].trim()), decls, order: rules.length });
  }
  return rules;
}

function splitTop(s) {
  const out = []; let depth = 0, cur = "";
  for (const ch of s) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (ch === "," && !depth) { out.push(cur.trim()); cur = ""; } else cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

// Whether a compound selector matches the root element with these
// attributes, and its specificity (the class-level count; :where() adds
// nothing, :not() and :is() their most specific argument).
function test(sel, attrs) {
  let ok = true, spec = 0, rest = sel.trim();
  while (rest) {
    let m;
    if ((m = rest.match(/^:root/))) spec++;
    else if ((m = rest.match(/^\[([\w-]+)(?:="([^"]*)")?\]/))) {
      if (m[2] === undefined ? !(m[1] in attrs) : attrs[m[1]] !== m[2]) ok = false;
      spec++;
    } else if ((m = rest.match(/^:(where|not|is)\(/))) {
      let depth = 1, i = m[0].length;
      for (; i < rest.length && depth; i++) { if (rest[i] === "(") depth++; if (rest[i] === ")") depth--; }
      const args = splitTop(rest.slice(m[0].length, i - 1)).map((s) => test(s, attrs));
      const any = args.some((a) => a.ok);
      if (m[1] === "not" ? any : !any) ok = false;
      if (m[1] !== "where") spec += Math.max(...args.map((a) => a.spec));
      rest = rest.slice(i);
      continue;
    } else if ((m = rest.match(/^html/))) spec += 0;
    // Another element or a class (the type sizes' control-reset list) is
    // never the root: it can't match, whatever else the compound says.
    else if ((m = rest.match(/^(?:[a-z][\w-]*|\.[\w-]+)/i))) { ok = false; spec++; }
    else throw new Error(`tokens.css: unsupported selector "${sel}"`);
    rest = rest.slice(m[0].length);
  }
  return { ok, spec };
}
const match = (sel, attrs) => { const r = test(sel, attrs); return r.ok ? r.spec : -1; };

// The declared (unresolved) value of every custom property for one theme.
export function declared(rules, theme, scheme = THEME_SCHEMES[theme]) {
  const attrs = { "data-theme": theme, "data-scheme": scheme };
  const win = {};
  for (const r of rules) {
    const spec = Math.max(...r.selectors.map((s) => match(s, attrs)));
    if (spec < 0) continue;
    for (const [k, v] of Object.entries(r.decls)) {
      const prev = win[k];
      if (!prev || spec > prev.spec || (spec === prev.spec && r.order >= prev.order)) win[k] = { v, spec, order: r.order };
    }
  }
  return Object.fromEntries(Object.entries(win).map(([k, { v }]) => [k, v]));
}

// ---- colour --------------------------------------------------------------

const toLin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
const fromLin = (v) => 255 * (v <= 0.0031308 ? 12.92 * v : 1.055 * Math.sign(v) * Math.abs(v) ** (1 / 2.4) - 0.055);
function oklab([r, g, b]) {
  [r, g, b] = [r, g, b].map(toLin);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}
function fromOklab([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3, m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3, s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s]
    .map((v) => Math.max(0, Math.min(255, fromLin(v))));
}
const NAMED = { transparent: [0, 0, 0, 0], black: [0, 0, 0, 1], white: [255, 255, 255, 1] };

// [r, g, b, a] (0–255, alpha 0–1) of a resolved colour value, or null.
export function parseColor(v) {
  v = v.trim().toLowerCase();
  if (NAMED[v]) return NAMED[v];
  let m;
  if ((m = v.match(/^#([0-9a-f]{3,8})$/))) {
    let h = m[1];
    if (h.length <= 4) h = [...h].map((c) => c + c).join("");
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)).concat(h.length === 8 ? parseInt(h.slice(6), 16) / 255 : 1);
  }
  if ((m = v.match(/^rgba?\(([^)]*)\)$/))) {
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p[3] ?? 1];
  }
  if ((m = v.match(/^color-mix\(\s*in\s+(oklab|srgb)\s*,(.*)\)$/))) {
    const [a, b] = splitTop(m[2]).map((part) => {
      const pm = part.match(/^(.*?)(?:\s+([\d.]+)%)?$/);
      return { c: parseColor(pm[1]), p: pm[2] == null ? null : +pm[2] / 100 };
    });
    if (!a.c || !b.c) return null;
    let p1 = a.p, p2 = b.p;
    if (p1 == null && p2 == null) p1 = p2 = 0.5;
    else if (p1 == null) p1 = 1 - p2;
    else if (p2 == null) p2 = 1 - p1;
    const sum = p1 + p2, mult = Math.min(1, sum);
    p1 /= sum; p2 /= sum;
    const space = m[1] === "oklab" ? (c) => oklab(c) : (c) => c.slice(0, 3);
    const A = space(a.c).map((x) => x * a.c[3]), B = space(b.c).map((x) => x * b.c[3]);
    const alpha = a.c[3] * p1 + b.c[3] * p2;
    if (!alpha) return [0, 0, 0, 0];
    const mixed = A.map((x, i) => (x * p1 + B[i] * p2) / alpha);
    return (m[1] === "oklab" ? fromOklab(mixed) : mixed).concat(alpha * mult);
  }
  return null;
}

// Substitute var() references until none are left.
export function substitute(decls, value, depth = 0) {
  if (depth > 40) throw new Error(`tokens.css: var() cycle in "${value}"`);
  const i = value.indexOf("var(");
  if (i < 0) return value;
  let d = 1, j = i + 4;
  for (; j < value.length && d; j++) { if (value[j] === "(") d++; if (value[j] === ")") d--; }
  const [name, ...fb] = splitTop(value.slice(i + 4, j - 1));
  const got = decls[name.trim()] ?? (fb.length ? fb.join(",") : null);
  if (got == null) return null;
  const inner = substitute(decls, got, depth + 1);
  if (inner == null) return null;
  return substitute(decls, value.slice(0, i) + inner + value.slice(j), depth + 1);
}

// Every custom property of a theme, resolved to a string, plus `colors`:
// the colour-valued ones as [r, g, b, a].
export function resolveTheme(theme, css = fs.readFileSync(TOKENS_CSS, "utf8"), scheme) {
  const decls = declared(parseRules(css), theme, scheme);
  const values = {}, colors = {};
  for (const k of Object.keys(decls)) {
    values[k] = substitute(decls, decls[k]);
    const c = values[k] == null ? null : parseColor(values[k]);
    if (c) colors[k] = c;
  }
  return { values, colors };
}

// ---- contrast ------------------------------------------------------------

const over = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
const lum = ([r, g, b]) => 0.2126 * toLin(r) + 0.7152 * toLin(g) + 0.0722 * toLin(b);
// WCAG ratio of fg on bg; a translucent bg sits on the theme's ground.
export function contrast(fg, bg, ground = [255, 255, 255, 1]) {
  const B = over(bg, over(ground, [255, 255, 255, 1]));
  const x = lum(over(fg, B)), y = lum(B);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

// { "theme": { "text-muted / bg-surface": ratio } } for every pair.
export function contrastTable(css) {
  const out = {};
  for (const theme of Object.keys(THEME_SCHEMES)) {
    const { colors } = resolveTheme(theme, css);
    out[theme] = {};
    for (const [fg, bg] of PAIRS) {
      if (!colors[fg] || !colors[bg]) throw new Error(`${theme}: ${!colors[fg] ? fg : bg} does not resolve to a colour`);
      out[theme][`${fg.slice(2)} / ${bg.slice(2)}`] = +contrast(colors[fg], colors[bg], colors["--bg-surface"]).toFixed(2);
    }
  }
  return out;
}

// The pairs below their minimum: { theme: { pair: ratio } } — what the
// baseline records.
export function failures(table) {
  const min = Object.fromEntries(PAIRS.map(([fg, bg, m]) => [`${fg.slice(2)} / ${bg.slice(2)}`, m]));
  const out = {};
  for (const [theme, row] of Object.entries(table)) {
    for (const [pair, r] of Object.entries(row)) if (r < min[pair]) (out[theme] ||= {})[pair] = r;
  }
  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const table = contrastTable(fs.readFileSync(TOKENS_CSS, "utf8"));
  const fails = failures(table);
  if (process.argv.includes("--baseline")) {
    fs.writeFileSync(BASELINE, JSON.stringify(fails, null, 2) + "\n");
    console.log(`wrote ${path.relative(FRONTEND, BASELINE)}`);
  }
  const themes = Object.keys(table);
  console.log("pair".padEnd(30) + themes.map((t) => t.padStart(12)).join(""));
  for (const pair of Object.keys(table[themes[0]])) {
    console.log(pair.padEnd(30) + themes.map((t) => `${table[t][pair].toFixed(2)}${fails[t]?.[pair] ? "✗" : " "}`.padStart(12)).join(""));
  }
  const n = Object.values(fails).reduce((s, f) => s + Object.keys(f).length, 0);
  console.log(`${n} of ${themes.length * PAIRS.length} below their minimum`);
}
