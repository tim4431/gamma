// Copies the design tokens and the interface font into the two shells that
// load their pages from disk: the desktop shell and the browser extension
// (docs/dev/ui-design.md, "Tokens"; desktop/docs/architecture.md;
// docs/dev/extension.md).
//
//   node tools/copy-tokens.mjs    # (npm run copy-tokens)
//
// The desktop chrome (desktop/ui/bar.html, launcher.html) and the extension's
// popup and options page can't import the frontend's stylesheet or the
// bundled font, so each keeps committed copies. tests/themes.test.mjs fails
// while a copy differs from its source, naming this command.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const INTER = path.join(FRONTEND, "node_modules", "@fontsource-variable", "inter");
const TOKENS = path.join(FRONTEND, "src", "shared", "styles", "tokens.css");

// [source, copy] per shell — the font is the Latin subset only (upright):
// the shells' words are server, workspace and folder names; other scripts
// use the system face.
const copiesInto = (dir) => [
  [TOKENS, path.join(dir, "tokens.css")],
  [path.join(INTER, "files", "inter-latin-wght-normal.woff2"), path.join(dir, "fonts", "inter-latin-wght-normal.woff2")],
  [path.join(INTER, "LICENSE"), path.join(dir, "fonts", "OFL.txt")],
];
export const TOKEN_COPIES = [
  ...copiesInto(path.join(FRONTEND, "..", "desktop", "ui")),
  ...copiesInto(path.join(FRONTEND, "..", "extension")),
];

// Whether a copy matches its source. A checkout may turn LF into CRLF in
// text files; the bytes that matter don't.
export const sameCopy = (from, to) => fs.existsSync(to) && (/\.(css|txt)$/.test(to)
  ? fs.readFileSync(from, "utf8").replace(/\r\n/g, "\n") === fs.readFileSync(to, "utf8").replace(/\r\n/g, "\n")
  : fs.readFileSync(from).equals(fs.readFileSync(to)));

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  for (const [from, to] of TOKEN_COPIES) {
    if (sameCopy(from, to)) continue;
    fs.mkdirSync(path.dirname(to), { recursive: true });
    fs.copyFileSync(from, to);
    console.log(`${path.relative(FRONTEND, from)} -> ${path.relative(FRONTEND, to)}`);
  }
}
