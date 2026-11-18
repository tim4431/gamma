// The sheets of the design pages (docs/design/look.md): every theme drawn
// from its own tokens, the default colour palette from the palette module,
// and the file icons from their kinds' tables, so the pictures cannot drift
// from the code. tests/themes.test.mjs fails while a committed sheet
// differs from what this writes.
//
//   node tools/designSheets.mjs   # write docs/design/assets/themes.svg, palette.svg and file-icons.svg
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_PALETTE, colorName, darkPageColor, darkPageLine, lineColor, swatchCss } from "../src/shared/model/palette.js";
import { EXTENSIONS, FILE_NAMES, FILE_STEMS, FOLDER_MARK_SHIFT, FOLDER_NAMES, FOLDER_PATH, FOLD_PATH, KIND_MARKS, KIND_TONES, PDF_BAND,
  SHEET_PATH } from "../src/shared/ui/fileIcons.js";
import { THEME_CHOICES } from "../src/settings/themeChoices.js";
import { THEME_SCHEMES, resolveTheme } from "./themes.mjs";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const SHEETS_DIR = path.resolve(FRONTEND, "..", "docs", "design", "assets");

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const hex2 = (n) => Math.round(n).toString(16).padStart(2, "0");
// A resolved colour ([r, g, b, a]) as SVG takes it: #rrggbb, #rrggbbaa when translucent.
const css = ([r, g, b, a]) => `#${hex2(r)}${hex2(g)}${hex2(b)}${a < 1 ? hex2(a * 255) : ""}`;

// A theme's colour tokens, resolved once; a token that does not resolve is named.
function themeColors(theme) {
  const { colors } = resolveTheme(theme);
  return (token) => {
    if (!colors[token]) throw new Error(`${theme}: ${token} does not resolve to a colour`);
    return css(colors[token]);
  };
}

// The labels around the pictures follow the reader's colour scheme, in the
// Gamma themes' text and border colours; what a picture shows keeps its own,
// or follows the scheme too by `rules(c)`, more rules in a theme's colours.
function head(w, h, title, rules = () => "") {
  const [light, dark] = [themeColors("gamma-light"), themeColors("gamma-dark")];
  const rule = (c) => `.l { fill: ${c("--text-primary")}; } .m { fill: ${c("--text-muted")}; } .rim { stroke: ${c("--border")}; }${rules(c)}`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}" font-family="Inter, 'Segoe UI', system-ui, -apple-system, sans-serif">
<title>${esc(title)}</title>
<style>
${rule(light)}
@media (prefers-color-scheme: dark) { ${rule(dark)} }
.mono { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; }
</style>
`;
}

// A theme's card: the chrome with its tab strip, the content under it, the
// focused block, the button hierarchy and the three role colours.
const CARD_W = 216, CARD_H = 136;
function card(c, id) {
  return `<clipPath id="${id}"><rect width="${CARD_W}" height="${CARD_H}" rx="12"/></clipPath>
<g clip-path="url(#${id})">
<rect width="${CARD_W}" height="${CARD_H}" fill="${c("--bg-page")}"/>
<rect x="10" y="7" width="76" height="28" rx="6" fill="${c("--bg-surface")}"/>
<text x="20" y="25" font-size="10" font-weight="500" fill="${c("--text-primary")}">Paper</text>
<text x="98" y="25" font-size="10" font-weight="500" fill="${c("--text-muted")}">Notes</text>
<rect y="31" width="${CARD_W}" height="${CARD_H - 31}" fill="${c("--bg-surface")}"/>
<text x="14" y="53" font-size="13" font-weight="600" fill="${c("--text-primary")}">Reading notes</text>
<text x="14" y="69" font-size="10" fill="${c("--text-secondary")}">A highlight becomes a note</text>
<rect x="8" y="76" width="${CARD_W - 16}" height="20" rx="6" fill="${c("--bg-selected")}"/>
<text x="14" y="90" font-size="10" fill="${c("--text-primary")}">The focused block</text>
<text x="${CARD_W - 14}" y="90" font-size="10" text-anchor="end" fill="${c("--text-muted")}">p. 3</text>
<rect x="14" y="104" width="50" height="22" rx="6" fill="${c("--accent-fill")}"/>
<text x="39" y="119" font-size="10" font-weight="500" text-anchor="middle" fill="${c("--on-accent")}">Save</text>
<rect x="70.5" y="104.5" width="57" height="21" rx="6" fill="${c("--bg-elevated")}" stroke="${c("--border")}"/>
<text x="99" y="119" font-size="10" font-weight="500" text-anchor="middle" fill="${c("--text-primary")}">Cancel</text>
<text x="137" y="119" font-size="10" font-weight="500" fill="${c("--accent")}">Open</text>
<circle cx="180" cy="115.5" r="4" fill="${c("--danger")}"/>
<circle cx="192" cy="115.5" r="4" fill="${c("--warn")}"/>
<circle cx="204" cy="115.5" r="4" fill="${c("--ok")}"/>
</g>
<rect x="0.5" y="0.5" width="${CARD_W - 1}" height="${CARD_H - 1}" rx="11.5" fill="none" stroke="${c("--border")}"/>`;
}

export function themesSheet() {
  const names = Object.fromEntries(THEME_CHOICES.map(([id, name]) => [id, name]));
  const slots = [...Object.keys(THEME_SCHEMES), "system"];
  const COLS = 4, GAP = 24, ROW = CARD_H + 60, PAD = 12;
  const w = COLS * CARD_W + (COLS - 1) * GAP + 2 * PAD;
  const h = Math.ceil(slots.length / COLS) * ROW - 20 + 2 * PAD;
  let out = head(w, h, "Gamma's themes: each one's chrome, content, focused block, buttons and role colours");
  slots.forEach((theme, i) => {
    if (!names[theme]) throw new Error(`settings/themeChoices.js names no theme ${theme}`);
    const x = PAD + (i % COLS) * (CARD_W + GAP), y = PAD + Math.floor(i / COLS) * ROW;
    out += `<g transform="translate(${x} ${y})">\n`;
    if (theme === "system") {
      // System is Light or Dark, as the device is set.
      out += `<clipPath id="sys-l"><rect width="${CARD_W / 2}" height="${CARD_H}"/></clipPath><clipPath id="sys-d"><rect x="${CARD_W / 2}" width="${CARD_W / 2}" height="${CARD_H}"/></clipPath>
<g clip-path="url(#sys-l)">${card(themeColors("light"), "card-sys-l")}</g>
<g clip-path="url(#sys-d)">${card(themeColors("dark"), "card-sys-d")}</g>\n`;
    } else {
      const c = themeColors(theme);
      out += `${card(c, `card-${theme}`)}\n`;
      // The theme's seeds: its ground, ink and accent.
      ["--ground", "--ink", "--accent"].forEach((token, k) => {
        out += `<rect x="${k * 72 + 0.5}" y="${CARD_H + 32.5}" width="10" height="10" rx="2" fill="${c(token)}" class="rim"/><text x="${k * 72 + 15}" y="${CARD_H + 41}" font-size="10" class="m mono">${c(token)}</text>\n`;
      });
    }
    out += `<text y="${CARD_H + 22}" font-size="13" font-weight="600" class="l">${esc(names[theme])}</text>\n</g>\n`;
  });
  return `${out}</svg>\n`;
}

// A page with Flip page colors on, as the viewer draws it (app.css,
// `.pdfViewer.pdfDark`: the page's ground and the ink of its text).
const FLIPPED_PAGE = "#0f0f0f", FLIPPED_INK = "#f0f0f0";

// The default palette: the Highlight group as a text highlight shows it
// (the wash, the line tone, and both on a flipped page), the Ink highlight
// group as highlighter strokes, the Pen group as pen strokes.
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
  // Swatches side by side across the sheet, `width` each.
  const across = (colors, width) => (i) => i * (width + (colors.length > 1 ? (W - colors.length * width) / (colors.length - 1) : 0));

  section("Highlight", `Text highlights: the wash at ${Math.round(highlight.colors[0].alpha * 100)} %, a line style in a deeper tone of its hue, and both re-derived on a page with flipped colours`);
  const HW = 222, TILE = (HW - 8) / 2, hx = across(highlight.colors, HW);
  const word = (tx, ty, wash, line, ink) => {
    body += `<rect x="${tx + 12}" y="${ty + 12}" width="60" height="18" rx="2" fill="${wash}"/>
<text x="${tx + 16}" y="${ty + 25}" font-size="12" fill="${ink}">passage</text>
<text x="${tx + 16}" y="${ty + 49}" font-size="12" fill="${ink}">passage</text>
<rect x="${tx + 16}" y="${ty + 52}" width="47" height="1.6" fill="${line}"/>\n`;
  };
  highlight.colors.forEach((swatch, i) => {
    const x = hx(i);
    body += `<rect x="${x + 0.5}" y="${y + 0.5}" width="${TILE - 1}" height="63" rx="8" fill="#ffffff" class="rim"/>
<rect x="${x + TILE + 8.5}" y="${y + 0.5}" width="${TILE - 1}" height="63" rx="8" fill="${FLIPPED_PAGE}" class="rim"/>\n`;
    word(x, y, swatchCss(swatch), lineColor(swatch.hex), "#1a1a1a");
    word(x + TILE + 8, y, darkPageColor(swatch.hex), darkPageLine(swatch.hex), FLIPPED_INK);
    label(x, y + 66, swatch.hex, HW);
  });
  y += 66 + 22 + 28;

  section("Ink highlight", `Highlighter strokes, at ${Math.round(inkHighlight.colors[0].alpha * 100)} % over the page`);
  const IW = 105, ix = across(inkHighlight.colors, IW);
  inkHighlight.colors.forEach((swatch, i) => {
    const x = ix(i);
    body += `<rect x="${x + 0.5}" y="${y + 0.5}" width="${IW - 1}" height="43" rx="8" fill="#ffffff" class="rim"/>
<path d="M${x + 16} ${y + 26} C ${x + 40} ${y + 16}, ${x + 64} ${y + 28}, ${x + 89} ${y + 18}" fill="none" stroke="${swatchCss(swatch)}" stroke-width="12" stroke-linecap="round"/>\n`;
    label(x, y + 46, swatch.hex, IW);
  });
  y += 46 + 22 + 28;

  section("Pen", "Pens and text boxes, opaque");
  const PW = 60, px = across(pen.colors, PW);
  pen.colors.forEach(({ hex }, i) => {
    const x = px(i);
    body += `<rect x="${x + 0.5}" y="${y + 0.5}" width="${PW - 1}" height="43" rx="8" fill="${hex === "#ffffff" ? "#3a3a38" : "#ffffff"}" class="rim"/>
<path d="M${x + 12} ${y + 28} c 6 -14, 10 -14, 12 -2 s 6 12, 12 -2 s 6 -10, 12 -4" fill="none" stroke="${hex}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>\n`;
    label(x, y + 46, hex, PW);
  });
  y += 46 + 22;

  return `${head(W, y, "Gamma's default colour palette: the Highlight, Ink highlight and Pen groups")}${body}</svg>\n`;
}

// The file icons' names in the catalog, in its order: the files by tint
// (a PDF, the writing, the code, the data, the media, the rest), then the
// directories ("" a folder of no kind).
const FILE_KIND_LABELS = {
  pdf: "PDF", doc: "Text", readme: "README", license: "License", tex: "TeX", references: "References", code: "Code",
  shell: "Shell script", notebook: "Notebook", diff: "Diff", table: "Table", data: "Dataset", json: "JSON",
  image: "Image", audio: "Audio", video: "Video", slides: "Slides", config: "Config", lock: "Lock, key", git: "Git",
  archive: "Archive", font: "Font", plain: "Other",
};
const FOLDER_KIND_LABELS = {
  "": "Folder", doc: "Notes", tex: "Paper", references: "References", code: "Code", notebook: "Notebooks",
  test: "Tests, experiments", data: "Data", results: "Results", image: "Figures", video: "Media", slides: "Talks",
  config: "Config", git: "Git", packages: "Build, packages", temp: "Temporary", archive: "Archive", lock: "Secrets",
};

// A kind's mark as SVG ([tag, attributes] lists, fileIcons.js KIND_MARKS).
const markSvg = (marks) => marks.map(([tag, attrs]) => `<${tag} ${Object.entries(attrs).map(([k, v]) => `${k}="${v}"`).join(" ")}/>`).join("");

// The first names a kind is known by, as the catalog lists them: up to
// four, in at most 30 characters.
function examples(tokens) {
  const out = [];
  for (const t of tokens) if (out.length < 4 && [...out, t].join(" ").length <= 30) out.push(t);
  return out.join(" ");
}

// The file icons: every kind of file on FileGlyph's sheet in its tint, every
// kind of directory in FolderKindIcon's folder in the accent, at 32 px with
// the app's 1.6 px stroke, in the reader's scheme's Gamma theme; each named
// with the first names that give it.
export function fileIconsSheet() {
  const W = 960, COLS = 4, CELL = W / COLS, ROW = 48, SIZE = 32;
  const fileKinds = Object.keys(FILE_KIND_LABELS), folderKinds = Object.keys(FOLDER_KIND_LABELS);
  const missing = Object.keys(KIND_MARKS).filter((k) => !fileKinds.includes(k) && !folderKinds.includes(k));
  if (missing.length) throw new Error(`the file icons' catalog names no ${missing.join(", ")}: add it to FILE_KIND_LABELS or FOLDER_KIND_LABELS`);
  const tones = (c) => ` .paper { fill: ${c("--bg-raised")}; } .t { color: ${c("--text-muted")}; } .t-pdf { color: ${c("--kind-pdf")}; }`
    + ` .t-code, .t-folder { color: ${c("--accent")}; } .t-data { color: ${c("--kind-data")}; } .t-media { color: ${c("--warn")}; }`;
  const icon = (x, y, tone, body) => `<g transform="translate(${x} ${y}) scale(${SIZE / 24})" class="${tone}" fill="none" stroke="currentColor" `
    + `stroke-width="${(1.6 * 24) / SIZE}" stroke-linecap="round" stroke-linejoin="round">${body}</g>\n`;
  let y = 0;
  let body = "";
  const section = (title, caption) => {
    body += `<text y="${y + 16}" font-size="14" font-weight="600" class="l">${esc(title)}</text>
<text y="${y + 34}" font-size="11" class="m">${esc(caption)}</text>\n`;
    y += 50;
  };
  const grid = (kinds, draw) => {
    kinds.forEach((kind, i) => {
      const x = (i % COLS) * CELL, top = y + Math.floor(i / COLS) * ROW;
      const [label, names] = draw(kind, x, top);
      body += `<text x="${x + SIZE + 12}" y="${top + 14}" font-size="12" font-weight="600" class="l">${esc(label)}</text>
<text x="${x + SIZE + 12}" y="${top + 29}" font-size="10" class="m mono">${esc(names)}</text>\n`;
    });
    y += Math.ceil(kinds.length / COLS) * ROW + 16;
  };

  section("Files", "A synced folder's file by its name: its kind's mark on the sheet, in its kind's tint");
  grid(fileKinds, (kind, x, top) => {
    body += icon(x, top, `t${KIND_TONES[kind] ? `-${KIND_TONES[kind]}` : ""}`,
      `<path d="${SHEET_PATH}" class="paper"/><path d="${FOLD_PATH}"/>${markSvg(kind === "pdf" ? [PDF_BAND] : KIND_MARKS[kind])}`);
    const names = [...(FILE_STEMS[kind] || []).slice(0, 1).map((stem) => `${stem.toUpperCase()}.*`),
      ...(EXTENSIONS[kind] || []).map((ext) => `.${ext}`), ...(FILE_NAMES[kind] || [])];
    return [FILE_KIND_LABELS[kind], examples(names)];
  });
  section("Folders", "A directory by its name, a leading dot or underscore aside: its kind's mark in the folder, in the accent");
  grid(folderKinds, (kind, x, top) => {
    body += icon(x, top, "t-folder", `<path d="${FOLDER_PATH}"/>${kind ? `<g transform="translate(0 ${FOLDER_MARK_SHIFT})">${markSvg(KIND_MARKS[kind])}</g>` : ""}`);
    return [FOLDER_KIND_LABELS[kind], kind ? examples(FOLDER_NAMES[kind].map((name) => `${name}/`)) : "any other name"];
  });
  y -= 16;

  return `${head(W, y, "Gamma's file icons: each kind of file and directory of a synced folder", tones)}${body}</svg>\n`;
}

export const SHEETS = { "themes.svg": themesSheet, "palette.svg": paletteSheet, "file-icons.svg": fileIconsSheet };

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  fs.mkdirSync(SHEETS_DIR, { recursive: true });
  for (const [name, draw] of Object.entries(SHEETS)) {
    fs.writeFileSync(path.join(SHEETS_DIR, name), draw());
    console.log(`wrote ${path.relative(process.cwd(), path.join(SHEETS_DIR, name))}`);
  }
}
