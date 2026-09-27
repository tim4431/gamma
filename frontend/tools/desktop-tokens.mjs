// Copies the design tokens and the interface font into the desktop shell
// (docs/dev/ui-design.md, "Tokens"; desktop/docs/architecture.md).
//
//   node tools/desktop-tokens.mjs    # (npm run desktop-tokens)
//
// The shell's chrome (desktop/ui/bar.html, launcher.html) loads its pages
// from disk, so it can't import the frontend's stylesheet or the bundled
// font: it keeps committed copies. tests/themes.test.mjs fails while a copy
// differs from its source, naming this command.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const INTER = path.join(FRONTEND, "node_modules", "@fontsource-variable", "inter");
const UI = path.join(FRONTEND, "..", "desktop", "ui");

// [source, copy] — the font is the Latin subset only (upright): the shell's
// words are server and workspace names; other scripts use the system face.
export const DESKTOP_COPIES = [
  [path.join(FRONTEND, "src", "shared", "styles", "tokens.css"), path.join(UI, "tokens.css")],
  [path.join(INTER, "files", "inter-latin-wght-normal.woff2"), path.join(UI, "fonts", "inter-latin-wght-normal.woff2")],
  [path.join(INTER, "LICENSE"), path.join(UI, "fonts", "OFL.txt")],
];

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  for (const [from, to] of DESKTOP_COPIES) {
    fs.mkdirSync(path.dirname(to), { recursive: true });
    fs.copyFileSync(from, to);
    console.log(`${path.relative(FRONTEND, from)} -> ${path.relative(FRONTEND, to)}`);
  }
}
