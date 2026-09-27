// The design tokens (src/shared/styles/tokens.css, docs/dev/ui-design.md
// "Tokens"): one file, shared by the app, the pre-paint and the desktop shell.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DESKTOP_COPIES } from "../tools/desktop-tokens.mjs";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => fs.readFileSync(path.join(FRONTEND, p), "utf8");

// A checkout may turn LF into CRLF in text files; the bytes that matter don't.
const same = (a, b) => (/\.(css|txt)$/.test(a)
  ? fs.readFileSync(a, "utf8").replace(/\r\n/g, "\n") === fs.readFileSync(b, "utf8").replace(/\r\n/g, "\n")
  : fs.readFileSync(a).equals(fs.readFileSync(b)));

test("the desktop shell carries the same tokens and font as the app", () => {
  for (const [from, to] of DESKTOP_COPIES) {
    assert.ok(fs.existsSync(to) && same(from, to),
      `${path.relative(FRONTEND, to)} differs from ${path.relative(FRONTEND, from)}: run \`npm run desktop-tokens\` in frontend/`);
  }
  for (const page of ["bar.html", "launcher.html"]) {
    const html = read(`../desktop/ui/${page}`);
    assert.ok(html.indexOf('href="tokens.css"') >= 0 && html.indexOf('href="tokens.css"') < html.indexOf('href="theme.css"'),
      `desktop/ui/${page} links tokens.css before theme.css`);
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
