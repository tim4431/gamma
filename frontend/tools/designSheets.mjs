// The colour sheets of the design pages (docs/design/look.md): every theme
// drawn from its own tokens, and the default colour palette from the palette
// module, so the pictures cannot drift from the code. tests/themes.test.mjs
// fails while a committed sheet differs from what this writes.
//
//   node tools/designSheets.mjs   # write docs/design/assets/themes.svg and palette.svg
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_PALETTE, colorName, darkPageColor, darkPageLine, lineColor } from "../src/shared/model/palette.js";
import { THEME_SCHEMES, resolveTheme } from "./themes.mjs";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const SHEETS_DIR = path.resolve(FRONTEND, "..", "docs", "design", "assets");

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const hex2 = (n) => Math.round(n).toString(16).padStart(2, "0");
const paint = ([r, g, b, a]) => (a < 1 ? `#${hex2(r)}${hex2(g)}${hex2(b)}" fill-opacity="${+a.toFixed(3)}` : `#${hex2(r)}${hex2(g)}${hex2(b)}`);
const hexOf = (c) => `#${hex2(c[0])}${hex2(c[1])}${hex2(c[2])}`;

// The labels around the pictures follow the reader's colour scheme; what a
// picture shows keeps its own colours.
const head = (w, h, title) => `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}" font-family="Inter, 'Segoe UI', system-ui, -apple-system, sans-serif">
<title>${esc(title)}</title>
<style>
.l { fill: #292822; } .m { fill: #6b6a65; } .rim { stroke: #0000001f; }
@media (prefers-color-scheme: dark) { .l { fill: #f0ede6; } .m { fill: #a9a69e; } .rim { stroke: #ffffff2e; } }
.mono { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; }
</style>
`;

// Each theme's name and line as Settings → Appearance lists them, read from
// that pane so the sheet says what the app says.
function themeNames() {
  const src = fs.readFileSync(path.join(FRONTEND, "src", "settings", "SettingsAppearance.jsx"), "utf8");
  return Object.fromEntries([...src.matchAll(/\["([\w-]+)", T\("([^"]+)"\), T\("([^"]+)"\)\]/g)].map((m) => [m[1], [m[2], m[3]]]));
}

// A theme's card: the chrome with its tab strip, the content under it, the
// focused block, the button hierarchy and the three role colours.
const CARD_W = 216, CARD_H = 136;
function card(theme, id) {
  const c = resolveTheme(theme).colors;
  const p = (k) => {
    if (!c[k]) throw new Error(`${theme}: ${k} does not resolve to a colour`);
    return paint(c[k]);
  };
  return `<clipPath id="${id}"><rect width="${CARD_W}" height="${CARD_H}" rx="12"/></clipPath>
<g clip-path="url(#${id})">
<rect width="${CARD_W}" height="${CARD_H}" fill="${p("--bg-page")}"/>
<rect x="10" y="7" width="76" height="28" rx="6" fill="${p("--bg-surface")}"/>
<text x="20" y="25" font-size="10" font-weight="500" fill="${p("--text-primary")}">Paper</text>
<text x="98" y="25" font-size="10" font-weight="500" fill="${p("--text-muted")}">Notes</text>
<rect y="31" width="${CARD_W}" height="${CARD_H - 31}" fill="${p("--bg-surface")}"/>
<text x="14" y="53" font-size="13" font-weight="600" fill="${p("--text-primary")}">Reading notes</text>
<text x="14" y="69" font-size="10" fill="${p("--text-secondary")}">A highlight becomes a note</text>
<rect x="8" y="76" width="${CARD_W - 16}" height="20" rx="6" fill="${p("--bg-selected")}"/>
<text x="14" y="90" font-size="10" fill="${p("--text-primary")}">The focused block</text>
<text x="${CARD_W - 14}" y="90" font-size="10" text-anchor="end" fill="${p("--text-muted")}">p. 3</text>
<rect x="14" y="104" width="50" height="22" rx="6" fill="${p("--accent-fill")}"/>
<text x="39" y="119" font-size="10" font-weight="500" text-anchor="middle" fill="${p("--on-accent")}">Save</text>
<rect x="70.5" y="104.5" width="57" height="21" rx="6" fill="${p("--bg-elevated")}" stroke="${p("--border")}"/>
<text x="99" y="119" font-size="10" font-weight="500" text-anchor="middle" fill="${p("--text-primary")}">Cancel</text>
<text x="137" y="119" font-size="10" font-weight="500" fill="${p("--accent")}">Open</text>
<circle cx="180" cy="115.5" r="4" fill="${p("--danger")}"/>
<circle cx="192" cy="115.5" r="4" fill="${p("--warn")}"/>
<circle cx="204" cy="115.5" r="4" fill="${p("--ok")}"/>
</g>
<rect x="0.5" y="0.5" width="${CARD_W - 1}" height="${CARD_H - 1}" rx="11.5" fill="none" stroke="${p("--border")}"/>`;
}

const seeds = (theme) => {
  const c = resolveTheme(theme).colors;
  return ["--ground", "--ink", "--accent"].map((k) => hexOf(c[k]));
};

export function themesSheet() {
  const names = themeNames();
  const themes = Object.keys(THEME_SCHEMES);
  const slots = [...themes, "system"];
  const COLS = 4, GAP = 24, ROW = CARD_H + 76, PAD = 12;
  const w = COLS * CARD_W + (COLS - 1) * GAP + 2 * PAD;
  const h = Math.ceil(slots.length / COLS) * ROW - 20 + 2 * PAD;
  let out = head(w, h, "Gamma's themes: each one's chrome, content, focused block, buttons and role colours");
  slots.forEach((theme, i) => {
    const [name, line] = names[theme] || [];
    if (!name) throw new Error(`settings/SettingsAppearance.jsx lists no name for the theme ${theme}`);
    const x = PAD + (i % COLS) * (CARD_W + GAP), y = PAD + Math.floor(i / COLS) * ROW;
    out += `<g transform="translate(${x} ${y})">\n`;
    if (theme === "system") {
      // System is Light or Dark, as the device is set.
      out += `<clipPath id="sys-l"><rect width="${CARD_W / 2}" height="${CARD_H}"/></clipPath><clipPath id="sys-d"><rect x="${CARD_W / 2}" width="${CARD_W / 2}" height="${CARD_H}"/></clipPath>
<g clip-path="url(#sys-l)">${card("light", "card-sys-l")}</g>
<g clip-path="url(#sys-d)">${card("dark", "card-sys-d")}</g>\n`;
    } else {
      out += `${card(theme, `card-${theme}`)}\n`;
    }
    out += `<text y="${CARD_H + 22}" font-size="13" font-weight="600" class="l">${esc(name)}</text>
<text y="${CARD_H + 39}" font-size="11" class="m">${esc(line)}</text>\n`;
    if (theme !== "system") {
      seeds(theme).forEach((hex, k) => {
        out += `<rect x="${k * 72}.5" y="${CARD_H + 48}.5" width="10" height="10" rx="2" fill="${hex}" class="rim"/><text x="${k * 72 + 15}" y="${CARD_H + 57}" font-size="10" class="m mono">${hex}</text>\n`;
      });
    }
    out += "</g>\n";
  });
  return `${out}</svg>\n`;
}

// The default palette: the Highlight group as a text highlight shows it
// (the wash, the line tone, and both on a page with flipped colours), the
// Ink highlight group as highlighter strokes, the Pen group as pen strokes.
const FLIPPED_PAGE = "#1c1c1c";
export function paletteSheet() {
  const [highlight, inkHighlight, pen] = ["highlight", "ink-highlight", "pen"].map((id) => DEFAULT_PALETTE.groups.find((g) => g.id === id));
  const W = 960;
  let y = 0;
  let body = "";
  const section = (title, caption) => {
    body += `<text y="${y + 16}" font-size="14" font-weight="600" class="l">${esc(title)}</text>
<text y="${y + 34}" font-size="11" class="m">${esc(caption)}</text>\n`;
    y += 48;
  };
  const label = (x, top, hex, width) => {
    body += `<text x="${x}" y="${top + 15}" font-size="11" font-weight="500" class="l">${esc(colorName(hex))}</text>\n`;
    if (width >= 90) body += `<text x="${x + width}" y="${top + 15}" font-size="10" text-anchor="end" class="m mono">${hex}</text>\n`;
  };

  section("Highlight", `Text highlights: the wash at ${Math.round(highlight.colors[0].alpha * 100)} %, a line style in a deeper tone of its hue, and both re-derived on a page with flipped colours`);
  const HW = 222, HG = (W - 4 * HW) / 3, TILE = (HW - 8) / 2;
  highlight.colors.forEach(({ hex, alpha }, i) => {
    const x = i * (HW + HG);
    const word = (tx, ty, wash, line, ink) => {
      body += `<rect x="${tx + 12}" y="${ty + 12}" width="60" height="18" rx="2" fill="${wash}"/>
<text x="${tx + 16}" y="${ty + 25}" font-size="12" fill="${ink}">passage</text>
<text x="${tx + 16}" y="${ty + 49}" font-size="12" fill="${ink}">passage</text>
<rect x="${tx + 16}" y="${ty + 52}" width="47" height="1.6" fill="${line}"/>\n`;
    };
    body += `<rect x="${x + 0.5}" y="${y + 0.5}" width="${TILE - 1}" height="63" rx="8" fill="#ffffff" class="rim"/>
<rect x="${x + TILE + 8.5}" y="${y + 0.5}" width="${TILE - 1}" height="63" rx="8" fill="${FLIPPED_PAGE}" class="rim"/>\n`;
    word(x, y, `${hex}" fill-opacity="${alpha}`, lineColor(hex), "#1a1a1a");
    word(x + TILE + 8, y, darkPageColor(hex), darkPageLine(hex), "#e6e6e6");
    label(x, y + 66, hex, HW);
  });
  y += 66 + 22 + 28;

  section("Ink highlight", `Highlighter strokes, at ${Math.round(inkHighlight.colors[0].alpha * 100)} % over the page`);
  const n = inkHighlight.colors.length, IW = 105, IG = (W - n * IW) / (n - 1);
  inkHighlight.colors.forEach(({ hex, alpha }, i) => {
    const x = i * (IW + IG);
    body += `<rect x="${x + 0.5}" y="${y + 0.5}" width="${IW - 1}" height="43" rx="8" fill="#ffffff" class="rim"/>
<path d="M${x + 16} ${y + 26} C ${x + 40} ${y + 16}, ${x + 64} ${y + 28}, ${x + 89} ${y + 18}" fill="none" stroke="${hex}" stroke-opacity="${alpha}" stroke-width="12" stroke-linecap="round"/>\n`;
    label(x, y + 46, hex, IW);
  });
  y += 46 + 22 + 28;

  section("Pen", "Pens and text boxes, opaque");
  const m = pen.colors.length, PW = 60, PG = (W - m * PW) / (m - 1);
  pen.colors.forEach(({ hex }, i) => {
    const x = i * (PW + PG);
    body += `<rect x="${x + 0.5}" y="${y + 0.5}" width="${PW - 1}" height="43" rx="8" fill="${hex === "#ffffff" ? "#3a3a38" : "#ffffff"}" class="rim"/>
<path d="M${x + 12} ${y + 28} c 6 -14, 10 -14, 12 -2 s 6 12, 12 -2 s 6 -10, 12 -4" fill="none" stroke="${hex}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>\n`;
    label(x, y + 46, hex, PW);
  });
  y += 46 + 22;

  return `${head(W, y, "Gamma's default colour palette: the Highlight, Ink highlight and Pen groups")}${body}</svg>\n`;
}

export const SHEETS = { "themes.svg": themesSheet, "palette.svg": paletteSheet };

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  fs.mkdirSync(SHEETS_DIR, { recursive: true });
  for (const [name, draw] of Object.entries(SHEETS)) {
    fs.writeFileSync(path.join(SHEETS_DIR, name), draw());
    console.log(`wrote ${path.relative(process.cwd(), path.join(SHEETS_DIR, name))}`);
  }
}
