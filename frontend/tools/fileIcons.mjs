// Material Icon Theme's icons for a synced folder's files (docs/dev/
// ui-design.md "File icons"): the Vite plugin that ships the theme, a dev
// dependency pinned exactly (package.json).
//
//   build  every SVG the theme's manifest names, as dist/assets/file-icons/
//          <version>/<id>.svg, with the theme's LICENSE beside them. Files
//          under assets/ are cached as immutable (gamma/app.py), and the
//          version in the path is what a theme update changes; precompress
//          (vite.config.js) copies the larger ones.
//   map    the virtual module `virtual:file-icons`: {version, default: the
//          compact map}, which shared/ui/fileIcons.js imports with a literal
//          import(), so it is a lazy chunk and never in startup.
//   dev    the dev server answers the same paths from the package itself.
//
// The SVGs are never imported: Vite would inline the small ones as base64.
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const THEME = "material-icon-theme";
export const VIRTUAL = "virtual:file-icons";
const RESOLVED = `\0${VIRTUAL}`;

// The theme's package directory and version.
export const themeDir = () => path.dirname(require.resolve(`${THEME}/package.json`));
export const themeVersion = () => JSON.parse(fs.readFileSync(path.join(themeDir(), "package.json"), "utf8")).version;

// The theme's manifest as VS Code reads it with its default settings
// (generateManifest(), equal to the package's dist/material-icons.json).
export const themeManifest = () => require(THEME).generateManifest();

const lowered = (table) => Object.fromEntries(Object.entries(table || {}).map(([key, id]) => [key.toLowerCase(), id]));
const tables = (m) => ({
  fileExtensions: lowered(m.fileExtensions), fileNames: lowered(m.fileNames),
  folderNames: lowered(m.folderNames), languageIds: lowered(m.languageIds),
});

// What fileIcons.js matches against: the defaults' ids (`file`, `folder`,
// `rootFolder`), the four tables with their keys lowercased (VS Code
// matches ignoring case, and no two keys meet when lowercased), and the
// light theme's overlay of the same tables. An open folder's id is derived
// (folder-x-open, folder-x-open_light), so the expanded tables are left out.
export function compactMap(m) {
  return { file: m.file, folder: m.folder, rootFolder: m.rootFolder, ...tables(m), light: tables(m.light) };
}

// [id, the SVG's path in the package] for every icon the manifest names.
// Most are <id>.svg, some not (latex is latex.clone.svg): each is shipped
// as <id>.svg.
export function iconFiles(m, dir = themeDir()) {
  return Object.entries(m.iconDefinitions).map(([id, def]) => [id, path.join(dir, "icons", path.basename(def.iconPath))]);
}

export default function fileIcons() {
  const dir = themeDir();
  const version = themeVersion();
  const base = `assets/file-icons/${version}/`;
  let manifest = null;
  const read = () => (manifest ||= themeManifest());
  return {
    name: "gamma-file-icons",
    resolveId: (id) => (id === VIRTUAL ? RESOLVED : null),
    load(id) {
      if (id !== RESOLVED) return null;
      return `export const version = ${JSON.stringify(version)};\nexport default ${JSON.stringify(compactMap(read()))};\n`;
    },
    generateBundle() {
      for (const [id, file] of iconFiles(read(), dir)) {
        this.emitFile({ type: "asset", fileName: `${base}${id}.svg`, source: fs.readFileSync(file) });
      }
      this.emitFile({ type: "asset", fileName: `${base}LICENSE`, source: fs.readFileSync(path.join(dir, "LICENSE")) });
    },
    configureServer(server) {
      const files = new Map([...iconFiles(read(), dir).map(([id, file]) => [`/${base}${id}.svg`, file]),
        [`/${base}LICENSE`, path.join(dir, "LICENSE")]]);
      server.middlewares.use((req, res, next) => {
        const file = files.get((req.url || "").split("?")[0]);
        if (!file) return next();
        res.setHeader("Content-Type", file.endsWith(".svg") ? "image/svg+xml" : "text/plain; charset=utf-8");
        fs.createReadStream(file).pipe(res);
      });
    },
  };
}
