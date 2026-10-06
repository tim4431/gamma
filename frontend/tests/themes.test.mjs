// The design tokens (src/shared/styles/tokens.css, docs/dev/ui-design.md
// "Tokens"): one file, shared by the app, the pre-paint and the desktop
// shell; every theme a few seeds plus a scheme's recipe; its text contrast
// held to a ratchet (tools/themes.mjs resolves the file like a browser).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { TOKEN_COPIES, sameCopy } from "../tools/copy-tokens.mjs";
import { BASELINE, contrastTable, failures, parseColor, parseRules, resolveTheme } from "../tools/themes.mjs";
import { DARK_THEMES, THEMES, UI_SCALE, themeScheme } from "../src/app/prefDefs.js";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => fs.readFileSync(path.join(FRONTEND, p), "utf8");
const TOKENS = read("src/shared/styles/tokens.css");
const PINNED = THEMES.filter((t) => t !== "system");
const hex = (c) => `#${c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, "0")).join("")}`;

// The evaluator agrees with the browser: these are Chromium's pixels.
test("the token evaluator mixes like a browser", () => {
  assert.deepEqual(parseColor("color-mix(in oklab, #ffffff 96%, #1a1a1a)").map(Math.round), [245, 245, 245, 1]);
  assert.deepEqual(parseColor("color-mix(in srgb, #3a7bd5 10.5%, transparent)").map((v) => +v.toFixed(3)), [58, 123, 213, 0.105]);
  assert.deepEqual(parseColor("rgba(42, 79, 122, 0.3)"), [42, 79, 122, 0.3]);
  assert.deepEqual(parseColor("#586e754d").map((v) => +v.toFixed(2)), [88, 110, 117, 0.3]);
});

test("every pinned theme is in tokens.css, on the scheme prefDefs gives it", () => {
  for (const theme of PINNED) {
    assert.match(TOKENS, new RegExp(`\\[data-theme="${theme}"\\]`), `${theme} has a block`);
    const { values, colors } = resolveTheme(theme, TOKENS);
    assert.equal(values["color-scheme"], themeScheme(theme), `${theme}: color-scheme`);
    const [r, g, b] = colors["--ground"];
    assert.equal(0.2126 * r + 0.7152 * g + 0.0722 * b < 128, themeScheme(theme) === "dark", `${theme}: the ground suits the scheme`);
  }
  // The copies of the theme lists outside the bundle.
  const list = (text, re) => JSON.parse(`[${text.match(re)[1].replace(/'/g, '"')}]`).sort();
  const html = read("index.html");
  const chrome = JSON.parse(html.match(/var chrome = (\{[^}]*\});/)[1]);
  assert.deepEqual(Object.keys(chrome).sort(), [...PINNED].sort(), "index.html's pre-paint: the pinned themes");
  for (const theme of PINNED) {
    assert.equal(chrome[theme], hex(resolveTheme(theme, TOKENS).colors["--bg-page"]), `index.html's pre-paint: ${theme}'s theme-color is its --bg-page`);
  }
  assert.deepEqual(list(html, /data-scheme", \[([^\]]*)\]\.includes/), [...DARK_THEMES].sort(), "index.html's pre-paint");
  for (const page of ["bar.html", "launcher.html"]) {
    assert.deepEqual(list(read(`../desktop/ui/${page}`), /const DARK_THEMES = \[([^\]]*)\]/), [...DARK_THEMES].sort(), `desktop/ui/${page}`);
  }
  // The pre-paint's interface-size bounds are UI_SCALE's.
  const [, min, max] = html.match(/s >= ([\d.]+) && s <= ([\d.]+)/);
  assert.deepEqual([+min, +max], [UI_SCALE.min, UI_SCALE.max], "index.html's pre-paint: the UI_SCALE bounds");
});

test("every colour token resolves in every theme", () => {
  const names = new Set(parseRules(TOKENS).flatMap((r) => Object.keys(r.decls)).filter((k) => k.startsWith("--")));
  for (const theme of PINNED) {
    const { values, colors } = resolveTheme(theme, TOKENS);
    for (const name of names) {
      if (/^--(radius|font|z-|shadow|space|ctl|dur|ease|fs|lh)/.test(name)) assert.ok(values[name], `${theme}: ${name}`);
      else assert.ok(colors[name], `${theme}: ${name} is ${values[name]}`);
    }
  }
});

// A ratchet, not a bar: tests/theme-contrast-baseline.json records the
// pairs that were already below WCAG AA when the themes moved to seeds;
// a change may raise any ratio but lower none, and a pair that passes must
// keep passing. After a deliberate improvement, `node tools/themes.mjs
// --baseline` records the new floor.
test("no theme's text contrast gets worse than the baseline", () => {
  const table = contrastTable(TOKENS);
  const now = failures(table);
  const base = JSON.parse(fs.readFileSync(BASELINE, "utf8"));
  const worse = [];
  for (const [theme, row] of Object.entries(now)) {
    for (const [pair, ratio] of Object.entries(row)) {
      const was = base[theme]?.[pair];
      if (was == null) worse.push(`${theme}: ${pair} ${ratio} (passed before)`);
      else if (ratio < was - 0.01) worse.push(`${theme}: ${pair} ${ratio} (was ${was})`);
    }
  }
  assert.deepEqual(worse, [], "contrast fell below the baseline (node tools/themes.mjs shows the table)");
});

test("the desktop shell and the extension carry the same tokens and font as the app", () => {
  for (const [from, to] of TOKEN_COPIES) {
    assert.ok(sameCopy(from, to),
      `${path.relative(FRONTEND, to)} differs from ${path.relative(FRONTEND, from)}: run \`npm run copy-tokens\` in frontend/`);
  }
  const linksBefore = (file, first, then) => {
    const html = read(file), a = html.indexOf(first), b = html.indexOf(then);
    assert.ok(a >= 0 && b >= 0 && a < b, `${file} loads ${first} before ${then}`);
  };
  for (const page of ["bar.html", "launcher.html"]) linksBefore(`../desktop/ui/${page}`, 'href="tokens.css"', 'href="theme.css"');
  // The extension sets the theme attributes before its stylesheets paint.
  for (const page of ["popup.html", "options.html"]) {
    linksBefore(`../extension/${page}`, 'src="theme.js"', 'href="tokens.css"');
    linksBefore(`../extension/${page}`, 'href="tokens.css"', 'href="popup.css"');
  }
  // The window's title-bar overlay continues the shell bar: each theme's chrome.
  const main = read("../desktop/main.js");
  for (const theme of PINNED) {
    const m = main.match(new RegExp(`'?${theme}'?: \\{ bg: '(#[0-9a-f]{6})'`));
    assert.ok(m, `desktop/main.js has a title-bar colour for ${theme}`);
    assert.equal(m[1], hex(resolveTheme(theme, TOKENS).colors["--bg-page"]), `desktop/main.js: ${theme}'s title bar is its --bg-page`);
  }
});

test("index.html links the tokens first and keeps no colour of its own", () => {
  const html = read("index.html");
  const head = html.slice(0, html.indexOf("</head>"));
  const link = head.indexOf('href="/src/shared/styles/tokens.css"');
  assert.ok(link > head.indexOf('setAttribute("data-theme"'), "the pre-paint script sets the theme before the tokens load");
  assert.ok(link < head.indexOf("<style>"), "the tokens are the first stylesheet");
  const style = head.slice(head.indexOf("<style>"), head.indexOf("</style>"));
  assert.doesNotMatch(style, /data-theme/, "no per-theme rule in the inline style");
  assert.doesNotMatch(style.replace(/\/\*[\s\S]*?\*\//g, "").replace(/#splash \.(mark|spin)[^}]*\}/g, ""), /#[0-9a-f]{3,8}\b/i,
    "the page and splash colours come from the tokens");
});

test("custom properties for colours are declared only in tokens.css", () => {
  const css = read("src/shared/styles/app.css").replace(/\/\*[\s\S]*?\*\//g, "");
  for (const name of ["--bg-page", "--bg-surface", "--text-primary", "--text-muted", "--border", "--accent", "--danger"]) {
    assert.doesNotMatch(css, new RegExp(`${name}\\s*:`), `${name} is declared in app.css`);
  }
});
