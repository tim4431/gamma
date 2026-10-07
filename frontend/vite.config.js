import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import zlib from "node:zlib";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// A Brotli (quality 11) and a gzip (level 9) copy beside every emitted file
// worth compressing, made once per build instead of per request: the backend
// sends the copy a request accepts (gamma/app.py), and so does a reverse proxy
// serving dist itself (docs/dev/debugging.md, "Serving the build").
const COMPRESSIBLE = /\.(js|mjs|css|html|svg)$/;
const MIN_BYTES = 1024;
const brotli = promisify(zlib.brotliCompress);
const gzip = promisify(zlib.gzip);

function precompress() {
  let outDir;
  return {
    name: "gamma-precompress",
    apply: "build",
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
    },
    async closeBundle() {
      const entries = await fs.readdir(outDir, { recursive: true, withFileTypes: true });
      const files = entries.filter((e) => e.isFile() && COMPRESSIBLE.test(e.name))
        .map((e) => path.join(e.parentPath, e.name));
      await Promise.all(files.map(async (file) => {
        const data = await fs.readFile(file);
        if (data.length < MIN_BYTES) return;
        const copies = await Promise.all([
          brotli(data, { params: {
            [zlib.constants.BROTLI_PARAM_QUALITY]: 11,
            [zlib.constants.BROTLI_PARAM_SIZE_HINT]: data.length,
          } }).then((body) => [".br", body]),
          gzip(data, { level: 9 }).then((body) => [".gz", body]),
        ]);
        await Promise.all(copies.filter(([, body]) => body.length < data.length)
          .map(([ext, body]) => fs.writeFile(file + ext, body)));
      }));
    },
  };
}

// The App chunk is imported once the locale catalog is in (main.jsx), a
// round trip after the entry. A modulepreload for it in the page's head
// starts that fetch with the entry's own, and its stylesheet goes in the
// head too (Vite's preload helper finds the link and adds no second one).
// Nothing the first screen paints changes, only when its bytes start
// arriving (docs/research/bundle.md).
function preloadApp() {
  return {
    name: "gamma-preload-app",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(html, ctx) {
        const bundle = ctx.bundle;
        if (!bundle) return;
        // By the module it holds: a dynamic entry's facade id is null here.
        const app = Object.values(bundle).find((c) => c.type === "chunk" && c.isDynamicEntry
          && Object.keys(c.modules).some((m) => /[\\/]src[\\/]app[\\/]App\.jsx$/.test(m)));
        if (!app) return;
        const tags = [{ tag: "link", attrs: { rel: "modulepreload", crossorigin: true, href: "/" + app.fileName }, injectTo: "head" }];
        for (const css of app.viteMetadata?.importedCss || []) {
          tags.push({ tag: "link", attrs: { rel: "stylesheet", crossorigin: true, href: "/" + css }, injectTo: "head" });
        }
        return tags;
      },
    },
  };
}

export default defineConfig({
  plugins: [react(), precompress(), preloadApp()],
  server: {
    proxy: {
      // ws: the page's live socket (/api/ws/page/…) rides the same proxy
      "/api": { target: "http://127.0.0.1:9001", ws: true },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    allowedHosts: ["annotation.amogadgetlab.com"]
  }
});
